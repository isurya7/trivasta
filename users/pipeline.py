from users.models import Profile


def save_profile(backend, user, response, is_new=False, *args, **kwargs):
    """Creates Profile for new OAuth users."""
    Profile.objects.get_or_create(user=user)


def send_welcome_email(backend, user, response, is_new=False, *args, **kwargs):
    """Fires welcome email only on first Google login."""
    if not is_new:
        return
    from users.emails import send_welcome_email as fire
    try:
        fire(user)
    except Exception:
        pass