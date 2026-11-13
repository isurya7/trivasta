from datetime import date

from django import forms

from .models import Trip
from .validation import (
    MAX_BUDGET, MAX_DAYS, MAX_PEOPLE, MAX_PREFS_LEN, MIN_DAYS, MIN_GROUP_TOTAL,
    MIN_PEOPLE, MIN_PER_PERSON, clean_multiline, clean_single_line,
)

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
        widget=forms.Select(attrs={'class': 'field-input'}),
    )
    travel_mode = forms.ChoiceField(
        choices=TRAVEL_MODES,
        widget=forms.Select(attrs={'class': 'field-input'}),
    )
    budget_type = forms.ChoiceField(
        choices=BUDGET_TYPES,
        widget=forms.RadioSelect(attrs={'class': 'budget-radio'}),
    )
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'field-input'}),
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'field-input'}),
    )
    # Not required at field level: when both dates are given, days is derived
    # from them. Bounds are enforced in clean().
    days = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={
            'placeholder': 'e.g. 7', 'min': MIN_DAYS, 'max': MAX_DAYS,
            'class': 'field-input',
        }),
    )
    # Length is checked in clean_additional_preferences (after newline
    # normalisation), so browsers' \n and form-posted \r\n count the same.
    additional_preferences = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 3, 'maxlength': MAX_PREFS_LEN, 'class': 'field-input',
            'placeholder': 'e.g. peaceful places over crowded spots, vegetarian food, '
                           'boutique stays, relaxed pace, avoid overnight journeys',
        }),
    )

    class Meta:
        model = Trip
        fields = [
            'origin', 'destination', 'travel_type', 'travel_mode',
            'start_date', 'end_date', 'days',
            'num_people', 'budget', 'budget_type',
            'additional_preferences',
        ]
        widgets = {
            'origin': forms.TextInput(attrs={
                'placeholder': 'e.g. Mumbai, India', 'class': 'field-input',
            }),
            'destination': forms.TextInput(attrs={
                'placeholder': 'e.g. Rajasthan, India', 'class': 'field-input',
            }),
            'num_people': forms.NumberInput(attrs={
                'placeholder': '1', 'min': MIN_PEOPLE, 'max': MAX_PEOPLE,
                'class': 'field-input',
            }),
            'budget': forms.NumberInput(attrs={
                'placeholder': 'e.g. 50000', 'min': 0, 'class': 'field-input',
            }),
        }

    # ── Field-level cleaning ──────────────────────────────────────────────
    def clean_destination(self):
        value = clean_single_line(self.cleaned_data.get('destination', ''))
        if not value:
            raise forms.ValidationError("Please enter a destination.")
        return value

    def clean_origin(self):
        return clean_single_line(self.cleaned_data.get('origin', ''))

    def clean_additional_preferences(self):
        value = clean_multiline(self.cleaned_data.get('additional_preferences', ''))
        if len(value) > MAX_PREFS_LEN:
            raise forms.ValidationError(
                f"Please keep this under {MAX_PREFS_LEN} characters (you have {len(value)})."
            )
        return value

    # ── Cross-field validation ────────────────────────────────────────────
    def clean(self):
        super().clean()
        cleaned = self.cleaned_data

        start = cleaned.get('start_date')
        end = cleaned.get('end_date')
        days = cleaned.get('days')
        people = cleaned.get('num_people')
        budget = cleaned.get('budget')
        budget_type = cleaned.get('budget_type')
        destination = cleaned.get('destination')
        origin = cleaned.get('origin')

        # Dates
        date_error = False
        if end and not start:
            self.add_error('start_date', "Please add a start date, or clear the end date.")
            date_error = True
        if start and start < date.today():
            self.add_error('start_date', "Start date can't be in the past.")
            date_error = True
        if start and end and end < start:
            self.add_error('end_date', "End date can't be before the start date.")
            date_error = True

        # Duration: dates win when both are given (start and end day both count).
        if start and end and not date_error:
            derived = (end - start).days + 1
            if derived > MAX_DAYS:
                self.add_error(
                    'end_date',
                    f"Your dates cover {derived} days; trips can be at most {MAX_DAYS} days.",
                )
            else:
                self.errors.pop('days', None)   # dates override whatever was typed
                self.cleaned_data['days'] = derived
        elif 'days' not in self.errors:
            if days is None:
                self.add_error('days', "Enter the number of days, or pick start and end dates.")
            elif not (MIN_DAYS <= days <= MAX_DAYS):
                self.add_error('days', f"Trips can be between {MIN_DAYS} and {MAX_DAYS} days.")

        # Travellers
        if people is not None and not (MIN_PEOPLE <= people <= MAX_PEOPLE):
            self.add_error('num_people', f"Travellers must be between {MIN_PEOPLE} and {MAX_PEOPLE}.")
            people = None

        # Budget
        if budget is not None:
            if budget <= 0:
                self.add_error('budget', "Budget must be greater than zero.")
            elif budget > MAX_BUDGET:
                self.add_error('budget', f"Budget can't exceed ₹{MAX_BUDGET:,}.")
            elif people is not None and budget_type in ('total', 'per_person'):
                if budget_type == 'per_person':
                    total, per_person = budget * people, budget
                else:
                    total = budget
                    per_person = budget // people if people > 1 else budget

                if people > 1 and total < MIN_GROUP_TOTAL:
                    self.add_error(
                        'budget',
                        f"Minimum budget for a group trip is ₹{MIN_GROUP_TOTAL:,}. "
                        f"Your current total is ₹{total:,}.",
                    )
                elif per_person < MIN_PER_PERSON:
                    self.add_error(
                        'budget',
                        f"Minimum budget per person is ₹{MIN_PER_PERSON:,}. "
                        f"Your current per-person budget is ₹{per_person:,}.",
                    )

        # Origin vs destination
        if origin and destination and origin.lower() == destination.lower():
            self.add_error('destination', "Origin and destination can't be the same.")

        return self.cleaned_data