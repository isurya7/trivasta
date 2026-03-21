from django import forms
from .models import Trip

TRAVEL_TYPES = [
    ('',          '— Select style —'),
    ('solo',      '🎒 Solo'),
    ('couple',    '💑 Couple'),
    ('family',    '👨‍👩‍👧 Family'),
    ('friends',   '👫 Friends'),
    ('adventure', '🏔️ Adventure'),
    ('business',  '💼 Business'),
    ('luxury',    '✨ Luxury'),
]

TRAVEL_MODES = [
    ('any',    '🚀 Any / Flexible'),
    ('flight', '✈️ Flight'),
    ('train',  '🚂 Train'),
    ('bus',    '🚌 Bus'),
    ('car',    '🚗 Self Drive'),
    ('cruise', '🚢 Cruise'),
]

BUDGET_TYPES = [
    ('total',      'Total budget for the trip'),
    ('per_person', 'Per person budget'),
]

class TripForm(forms.ModelForm):
    travel_type = forms.ChoiceField(
        choices=TRAVEL_TYPES,
        widget=forms.Select(attrs={'class': 'field-input'})
    )
    travel_mode = forms.ChoiceField(
        choices=TRAVEL_MODES,
        widget=forms.Select(attrs={'class': 'field-input'})
    )
    budget_type = forms.ChoiceField(
        choices=BUDGET_TYPES,
        widget=forms.RadioSelect(attrs={'class': 'budget-radio'})
    )
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'field-input'})
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'field-input'})
    )

    class Meta:
        model  = Trip
        fields = [
            'origin', 'destination', 'travel_type', 'travel_mode',
            'start_date', 'end_date', 'days',
            'num_people', 'budget', 'budget_type'
        ]
        widgets = {
            'origin': forms.TextInput(attrs={
                'placeholder': 'e.g. Mumbai, India',
                'class': 'field-input'
            }),
            'destination': forms.TextInput(attrs={
                'placeholder': 'e.g. Rajasthan, India',
                'class': 'field-input'
            }),
            'days': forms.NumberInput(attrs={
                'placeholder': 'e.g. 7',
                'min': 1, 'max': 30,
                'class': 'field-input'
            }),
            'num_people': forms.NumberInput(attrs={
                'placeholder': '1',
                'min': 1, 'max': 50,
                'class': 'field-input'
            }),
            'budget': forms.NumberInput(attrs={
                'placeholder': 'e.g. 50000',
                'min': 0,
                'class': 'field-input'
            }),
        }