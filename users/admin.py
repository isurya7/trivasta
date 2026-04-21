from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import Profile


class ProfileInline(admin.StackedInline):
    model      = Profile
    can_delete = False


class UserAdmin(BaseUserAdmin):
    inlines = [ProfileInline]


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
admin.site.register(Profile)


# Register ContactMessage if the model exists
try:
    from .models import ContactMessage

    @admin.register(ContactMessage)
    class ContactMessageAdmin(admin.ModelAdmin):
        list_display  = ('first_name', 'last_name', 'email', 'subject', 'is_read', 'created_at')
        list_filter   = ('subject', 'is_read')
        search_fields = ('first_name', 'last_name', 'email', 'message')
        readonly_fields = ('created_at',)
        list_editable = ('is_read',)
        ordering      = ('-created_at',)

except ImportError:
    pass