from django import forms
from django.core.exceptions import ValidationError

from eventyay.hackathon.calendar import attendance_hours


class CategorizedTags(forms.CheckboxSelectMultiple):
    template_name = 'hackathon/widgets/tags.html'


class HourGrid(forms.CheckboxSelectMultiple):
    template_name = 'hackathon/widgets/hours.html'


class UnavailableHoursField(forms.MultipleChoiceField):
    widget = HourGrid

    def __init__(self, *, hackathon, **kwargs):
        super().__init__(choices=attendance_hours(hackathon) if hackathon else [], required=False, **kwargs)

    def clean(self, value):
        return sorted(int(hour) for hour in super().clean(value))


class TagsField(forms.CharField):
    def clean(self, value):
        value = super().clean(value)
        tags = list(
            dict.fromkeys(tag.strip() for tag in value.replace('，', ',').replace('\n', ',').split(',') if tag.strip())
        )
        if any(len(tag) > 40 for tag in tags):
            raise ValidationError('每个标签最多 40 字；标签数量不限。')
        return tags
