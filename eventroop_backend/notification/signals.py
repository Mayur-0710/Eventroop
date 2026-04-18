"""
Wire up signals in your app's AppConfig.ready() or apps.py:

    class MyAppConfig(AppConfig):
        def ready(self):
            import notifications.signals  # noqa

Then uncomment and adapt the examples below for your own models.
"""
# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from notifications.views import create_notification

# Example – Like model
# @receiver(post_save, sender='myapp.Like')
# def on_like_created(sender, instance, created, **kwargs):
#     if not created:
#         return
#     create_notification(
#         recipient  = instance.post.author,
#         sender     = instance.user,
#         title      = "New Like ❤️",
#         message    = f"{instance.user.username} liked your post.",
#         notify_type = 'like',
#         data       = {'post_id': instance.post.id},
#         send_push  = True,
#     )

# Example – Comment model
# @receiver(post_save, sender='myapp.Comment')
# def on_comment_created(sender, instance, created, **kwargs):
#     if not created:
#         return
#     create_notification(
#         recipient  = instance.post.author,
#         sender     = instance.user,
#         title      = "New Comment 💬",
#         message    = f"{instance.user.username}: \"{instance.body[:80]}\"",
#         notify_type = 'comment',
#         data       = {'post_id': instance.post.id, 'comment_id': instance.id},
#         send_email = True,
#         send_push  = True,
#     )

# Example – System alert (call directly, no signal needed)
# create_notification(
#     recipient  = admin_user,
#     title      = "⚠️ High CPU Alert",
#     message    = "Server CPU exceeded 90% for 5 minutes.",
#     notify_type = 'alert',
# )