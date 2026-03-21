from django import forms
from django.contrib.auth.models import User
from .models import Agency, Package, Offer, PackageImage, PackageReview

class AgencyRegisterForm(forms.Form):
    username    = forms.CharField(max_length=150)
    email       = forms.EmailField()
    password    = forms.CharField(widget=forms.PasswordInput)
    name        = forms.CharField(max_length=255, label="Agency Name")
    phone       = forms.CharField(max_length=20)
    description = forms.CharField(widget=forms.Textarea)
    location    = forms.CharField(max_length=255)
    website     = forms.URLField(required=False)

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Username already taken.")
        return username

    def clean_email(self):
        email = self.cleaned_data['email']
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Email already registered.")
        if Agency.objects.filter(email=email).exists():
            raise forms.ValidationError("Agency with this email already exists.")
        return email


class AgencyProfileForm(forms.ModelForm):
    class Meta:
        model  = Agency
        fields = ['name', 'phone', 'description', 'location', 'website']
        widgets = {
            'name':        forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'Agency name'}),
            'phone':       forms.TextInput(attrs={'class': 'field-input', 'placeholder': '+91 98765 43210'}),
            'description': forms.Textarea(attrs={'class': 'field-input', 'rows': 4, 'placeholder': 'Tell travelers about your agency...'}),
            'location':    forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'e.g. Mumbai, India'}),
            'website':     forms.URLInput(attrs={'class': 'field-input', 'placeholder': 'https://youragency.com'}),
        }


class PackageImageForm(forms.ModelForm):
    class Meta:
        model  = PackageImage
        fields = ['image_url', 'image_file', 'caption', 'order']

PackageImageFormSet = forms.inlineformset_factory(
    Package, PackageImage,
    form=PackageImageForm,
    fields=['image_url', 'image_file', 'caption', 'order'],
    extra=3,
    can_delete=True,
    max_num=10,
)

class PackageForm(forms.ModelForm):
    class Meta:
        model  = Package
        fields = ['title', 'destination', 'description', 'duration',
                  'price', 'category', 'inclusions', 'image_url', 'is_active']
        widgets = {
            'title':       forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'e.g. Golden Triangle Tour'}),
            'destination': forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'e.g. Rajasthan, India'}),
            'description': forms.Textarea(attrs={'class': 'field-input', 'rows': 4, 'placeholder': 'Describe the package...'}),
            'duration':    forms.NumberInput(attrs={'class': 'field-input', 'placeholder': '7', 'min': 1}),
            'price':       forms.NumberInput(attrs={'class': 'field-input', 'placeholder': '25000', 'min': 0}),
            'category':    forms.Select(attrs={'class': 'field-input'}),
            'inclusions':  forms.Textarea(attrs={'class': 'field-input', 'rows': 3, 'placeholder': 'Hotel, Flights, Meals...'}),
            'image_url':   forms.URLInput(attrs={'class': 'field-input', 'placeholder': 'https://...'}),
        }

class PackageReviewForm(forms.ModelForm):
    class Meta:
        model  = PackageReview
        fields = ['rating', 'title', 'body']
        widgets = {
            'rating': forms.HiddenInput(),
            'title':  forms.TextInput(attrs={'class': 'field-input', 'placeholder': 'Summarise your experience'}),
            'body':   forms.Textarea(attrs={'class': 'field-textarea', 'rows': 4, 'placeholder': 'Tell others what you loved...'}),
        }


class OfferForm(forms.ModelForm):
    class Meta:
        model  = Offer
        fields = ['price', 'message']
        widgets = {
            'price':   forms.NumberInput(attrs={'class': 'field-input', 'placeholder': 'e.g. 45000'}),
            'message': forms.Textarea(attrs={'class': 'field-input', 'rows': 4, 'placeholder': 'Describe what you offer for this trip...'}),
        }