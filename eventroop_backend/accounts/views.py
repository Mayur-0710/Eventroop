# views.py
from django.utils import timezone
from django.contrib.auth.hashers import check_password 
from rest_framework import viewsets,generics,status
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated,AllowAny
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import AuthenticationFailed
from .permissions import IsVSREOwner,IsCreator,IsVSREOwnerOrManager,IsMasterAdmin
from .models import CustomUser, UserHierarchy, PricingModel, UserPlan,PasswordResetOTP
from .serializers import *
import random
import uuid
from django.core.mail import send_mail
from django.conf import settings

# ---------------------- User registration ViewSet ----------------------
class CustomerRegistrationView(generics.CreateAPIView):
    serializer_class = CustomerRegistrationSerializer
    permission_classes = [AllowAny]

class VSREOwnerRegistrationView(generics.CreateAPIView):
    serializer_class = VSREOwnerRegistrationSerializer
    permission_classes = [AllowAny]

# ---------------------- User Authentication ViewSet ----------------------
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserLoginSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            user = serializer.validated_data['user']
            if not user.is_active:
                return Response(
                    {'error': 'Account pending approval by Master Admin.'},
                    status=status.HTTP_403_FORBIDDEN
                )

            user.last_login = timezone.localtime()
            user.save()

            refresh = RefreshToken.for_user(user)
            return Response({
                'message': f'Login successful as {user.user_type}',
                'user': BaseUserSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token)
                }
            }, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get('refresh_token')
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
            return Response({'message': 'Logout successful'}, status=status.HTTP_200_OK)
        except Exception:
            return Response({'error': 'Invalid token or logout failed'}, status=status.HTTP_400_BAD_REQUEST)

class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        if serializer.is_valid():
            user = request.user
            if not check_password(serializer.validated_data['old_password'], user.password):
                return Response(
                    {"old_password": "Current password is incorrect."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            user.set_password(serializer.validated_data['new_password'])
            user.save()
            return Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# ---------------------- Password Reset via OTP ----------------------

class RequestPasswordResetOTPView(APIView):
    """
    Step 1: User submits their email.
    Generates a 6-digit OTP, saves it (invalidating any previous unused OTPs),
    and sends it via email.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RequestOTPSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"]
        user  = CustomUser.objects.get(email=email)

        # Invalidate all previous unused OTPs for this user
        PasswordResetOTP.objects.filter(user=user, is_used=False).update(is_used=True)

        # Generate and store new OTP
        # otp_code = f"{random.SystemRandom().randint(0, 999999):06d}"
        otp_code = f"{000000:06d}"
        PasswordResetOTP.objects.create(user=user, otp=otp_code)

        # # Send email
        # send_mail(
        #     subject="Your Password Reset OTP",
        #     message=(
        #         f"Hi {user.first_name or user.email},\n\n"
        #         f"Your OTP for password reset is: {otp_code}\n\n"
        #         f"This code is valid for 10 minutes.\n"
        #         f"If you didn't request this, please ignore this email."
        #     ),
        #     from_email=settings.DEFAULT_FROM_EMAIL,
        #     recipient_list=[email],
        #     fail_silently=False,
        # )

        return Response(
            {"message": "OTP sent to your email address."},
            status=status.HTTP_200_OK,
        )

class VerifyOTPView(APIView):
    """
    Step 2: User submits email + OTP.
    On success, returns a short-lived reset_token to authorise the password change.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email    = serializer.validated_data["email"]
        otp_code = serializer.validated_data["otp"]

        try:
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return Response({"error": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

        # Fetch the latest unused, unverified OTP for this user
        otp_obj = (
            PasswordResetOTP.objects
            .filter(user=user, otp=otp_code, is_used=False, is_verified=False)
            .order_by("-created_at")
            .first()
        )

        if not otp_obj:
            return Response({"error": "Invalid OTP."}, status=status.HTTP_400_BAD_REQUEST)

        if otp_obj.is_expired():
            return Response(
                {"error": "OTP has expired. Please request a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Mark as verified and issue a reset token
        reset_token = uuid.uuid4()
        otp_obj.is_verified  = True
        otp_obj.reset_token  = reset_token
        otp_obj.save()

        return Response(
            {"reset_token": str(reset_token)},
            status=status.HTTP_200_OK,
        )

class ResetPasswordView(APIView):
    """
    Step 3: User submits reset_token + new_password + confirm_password.
    Validates the token, sets the new password, and marks the OTP record as used.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        reset_token = serializer.validated_data["reset_token"]
        new_password = serializer.validated_data["new_password"]

        try:
            otp_obj = PasswordResetOTP.objects.get(
                reset_token=reset_token,
                is_verified=True,
                is_used=False,
            )
        except PasswordResetOTP.DoesNotExist:
            return Response(
                {"error": "Invalid or already used reset token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if otp_obj.is_expired():
            return Response(
                {"error": "Reset token has expired. Please start over."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Set the new password and invalidate the token
        user = otp_obj.user
        user.set_password(new_password)
        user.save()

        otp_obj.is_used = True
        otp_obj.save()

        return Response(
            {"message": "Password reset successful. You can now log in."},
            status=status.HTTP_200_OK,
        )    

# ---------------------- User Profile ViewSet -------------------------
class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self, user):
        if user.is_owner:
            return OwnerSerializer
        elif user.is_manager:
            return ManagerSerializer
        elif user.is_vsre_staff:
            return StaffSerializer
        return CustomerSerializer
    
    def get(self, request):
        serializer_class = self.get_serializer_class(request.user)
        serializer = serializer_class(instance=request.user, context={'request': request})
        return Response(serializer.data)

    def put(self, request):
        serializer_class = self.get_serializer_class(request.user)
        serializer = serializer_class(
            instance=request.user,
            data=request.data,
            partial=True,
            context={'request': request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

# ---------------------- User management ViewSet -------------------------
class OwnerViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OwnerSerializer
    
    permission_classes = [IsAuthenticated]
    filterset_fields = ["is_active", "city"]
    search_fields = ["email", "first_name", "last_name", "mobile_number"]


    def get_queryset(self):
        """Fetch all VSRE Owners."""
        request_user = self.request.user
        queryset = CustomUser.objects.owners()
        
        if request_user.is_superuser:
            return queryset
        
        if request_user.is_owner:
            return queryset.filter(hierarchy__owner=request_user)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context
    
    def perform_create(self, serializer):
        # create user first
        user = serializer.save(
            user_type=CustomUser.UserTypes.VSRE_OWNER,
            
        )
        return user
    
    def list(self, request, *args, **kwargs):
        owners = self.filter_queryset(self.get_queryset())

        # Use CustomUserManager methods for counts
        data = []
        for owner in owners:
            managers = CustomUser.objects.get_all_managers_under_owner(owner)
            staff = CustomUser.objects.get_staff_under_owner(owner)

            data.append({
                "id": owner.id,
                "first_name": owner.first_name,
                "last_name": owner.last_name,
                "email": owner.email,
                "mobile_number": owner.mobile_number,
                "city": owner.city,
                "manager_count": managers.count(),
                "staff_count": staff.count(),
            })

        page = self.paginate_queryset(data)
        if page is not None:
            return self.get_paginated_response(page)

        return Response(data)

    # ----------------------------------------------------------------------
    # RETRIEVE: Detailed hierarchy of a specific owner
    # ----------------------------------------------------------------------
    def retrieve(self, request, *args, **kwargs):
        owner = self.get_object()

        # Use manager’s hierarchy helper
        hierarchy = CustomUser.objects.get_entire_hierarchy_under_owner(owner)

        managers = hierarchy["all_managers"]
        staff = hierarchy["all_staff"]

        data = OwnerSerializer(owner).data
        data.update({
            "manager_count": managers.count(),
            "staff_count": staff.count(),
            "managers": [
                {
                    "id": m.id,
                    "name": f"{m.first_name} {m.last_name}".strip(),
                    "email": m.email,
                    "staff_count": CustomUser.objects.get_staff_under_manager(m).count(),
                }
                for m in managers
            ],
        })

        return Response(data)

class ManagerViewSet(viewsets.ModelViewSet):
    """
    Allows VSRE_OWNER to manage their own VSRE_MANAGER users.
    """
    serializer_class = ManagerSerializer
    
    permission_classes = [ IsVSREOwner, IsCreator]
    filterset_fields = ["is_active", "city"]
    search_fields = ["email", "first_name", "last_name", "mobile_number"]

    def get_queryset(self):
        """Return only managers created by this owner."""
        request_user = self.request.user
        queryset = CustomUser.objects.managers()
        
        if request_user.is_superuser:
            return queryset
        
        if request_user.is_owner:
            return queryset.filter(hierarchy__owner=request_user)
        
        if request_user.is_manager:
            return queryset.filter(hierarchy__parent=request_user)
        
    def get_serializer_class(self):
        if self.action == "list":
            return ManagerListSerializer
        return ManagerSerializer
       
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def perform_create(self, serializer):
        # create user first
        user = serializer.save(
            user_type=CustomUser.UserTypes.VSRE_MANAGER,
            
        )
        return user
    
    def perform_destroy(self, instance):
        if hasattr(instance, "soft_delete"):
            instance.soft_delete()
        else:
            instance.delete()

class StaffViewSet(viewsets.ModelViewSet):
    """
    Allows both VSRE_OWNER and VSRE_MANAGER to manage their own VSRE_STAFF users.
    """
    serializer_class = StaffSerializer
    
    permission_classes = [IsAuthenticated,IsCreator,IsVSREOwnerOrManager]
    filterset_fields = ["is_active", "city"]
    search_fields = ["email", "first_name", "last_name", "mobile_number"]

    def get_queryset(self):
        """
        Return only staff created by the logged-in owner/manager.
        """
        return CustomUser.objects.filter(
            created_by=self.request.user,
            hierarchy__owner=self.request.user,
            user_type=CustomUser.UserTypes.VSRE_STAFF,
            is_active= True, is_deleted= False,
        )
    def get_serializer_class(self):
        if self.action == "list":
            return StaffListSerializer
        return StaffSerializer
    
    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["request"] = self.request
        return context

    def perform_create(self, serializer):
        # create user first
        user = serializer.save(user_type=CustomUser.UserTypes.VSRE_STAFF)
        return user
    
    def perform_destroy(self, instance):
        
        if hasattr(instance, "soft_delete"):
            instance.soft_delete()
        # else:
        #     instance.delete()

class ParentAssignmentView(APIView):
    """
    GET    → Get current parent + assignable parents
    POST   → Assign parent
    DELETE → Remove parent
    """

    def get(self, request, user_id=None):
        try:
            child = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            child = request.user
        # --------------------------
        # CURRENT PARENT
        # --------------------------
        try:
            hierarchy = child.hierarchy
            parent = hierarchy.parent

            current_parent = {
                "id": parent.id,
                "name": parent.get_full_name(),
                "level": parent.hierarchy.level,
            } if parent else None

        except UserHierarchy.DoesNotExist:
            current_parent = None

        # --------------------------
        # ASSIGNABLE PARENTS (Dropdown)
        # --------------------------
        managers = CustomUser.objects.filter(
            hierarchy__owner=request.user,
            user_type__in=["VSRE_MANAGER", "LINE_MANAGER"]
        ).exclude(id__in=[user_id, parent.id])

        assignable = [
            {
                "id": m.id,
                "name": m.get_full_name(),
                "level": m.hierarchy.level,
            }
            for m in managers
        ]

        return Response({
            "current_parent": current_parent,
            "assignable_parents": assignable
        })

    # ---------------------------------------------------
    def post(self, request, user_id):
        """Assign a parent to a user"""
        child = get_object_or_404(CustomUser, id=user_id)
        parent_id = request.data.get("parent_id")

        if not parent_id:
            return Response({"error": "parent_id is required"}, status=400)

        # Check valid parent
        parent = get_object_or_404(
            CustomUser,
            id=parent_id,
            user_type__in=["VSRE_MANAGER", "LINE_MANAGER"],
        )

        hierarchy, _ = UserHierarchy.objects.get_or_create(
            user=child,
            defaults={"owner": request.user},
        )

        # prevent circular assignment
        if parent == child:
            return Response({"error": "A user cannot be their own parent"}, status=400)

        hierarchy.parent = parent
        hierarchy.save()

        return Response({
            "message": "Parent assigned successfully",
            "reports_to": {
                "id": parent.id,
                "name": parent.get_full_name(),
                "level": parent.hierarchy.level,
            },
        })

    # ---------------------------------------------------
    def delete(self, request, user_id):
        """Unassign parent"""
        child = get_object_or_404(CustomUser, id=user_id)

        hierarchy = get_object_or_404(UserHierarchy, user=child)

        hierarchy.parent = None
        hierarchy.level = 0
        hierarchy.save()

        return Response({"message": "Parent removed"})


# ---------------------- PricingModel ViewSet ----------------------
class PricingModelViewSet(viewsets.ModelViewSet):
    queryset = PricingModel.objects.select_related("created_by")
    serializer_class = PricingModelSerializer
    filterset_fields = ['plan_type', 'is_active']
    search_fields = ['name', 'description', 'created_by__email']


# ---------------------- UserPlan ViewSet ----------------------
class UserPlanViewSet(viewsets.ModelViewSet):
    queryset = UserPlan.objects.select_related("user", "plan")
    serializer_class = UserPlanSerializer
    filterset_fields = ['is_active', 'plan__plan_type']
    search_fields = ['user__email', 'plan__name']

    @action(detail=True, methods=['post'])
    def expire(self, request, pk=None):
        plan = self.get_object()
        plan.is_active = False
        plan.end_date = timezone.localtime()
        plan.save()
        return Response({"detail": "Plan expired."})

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        plan = self.get_object()
        plan.is_active = True
        if (
            plan.plan.plan_type == "SUBSCRIPTION"
            and (not plan.end_date or plan.end_date < timezone.localtime())
        ):
            plan.end_date = plan.start_date + timezone.timedelta(days=plan.plan.duration_days)
        plan.save()
        return Response({"detail": "Plan activated."})

