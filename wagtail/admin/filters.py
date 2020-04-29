import django_filters
from django import forms
from django.utils.translation import gettext_lazy as _
from django_filters.widgets import SuffixedMultiWidget

from wagtail.admin.staticfiles import versioned_static
from wagtail.admin.widgets import AdminDateInput, BooleanButtonSelect, ButtonSelect
from wagtail.core.models import Page, Task, TaskState, Workflow, WorkflowState


class DateRangePickerWidget(SuffixedMultiWidget):
    """
    A widget allowing a start and end date to be picked.
    """
    template_name = 'wagtailadmin/widgets/daterange_input.html'
    suffixes = ['after', 'before']

    def __init__(self, attrs=None):
        widgets = (AdminDateInput(attrs={'placeholder': _("Date from")}), AdminDateInput(attrs={'placeholder': _("Date to")}))
        super().__init__(widgets, attrs)

    def decompress(self, value):
        if value:
            return [value.start, value.stop]
        return [None, None]


class FilteredSelect(forms.Select):
    """
    A select box variant that adds 'data-' attributes to the <select> and <option> elements
    to allow the options to be dynamically filtered by another select box.
    See wagtailadmin/js/filtered-select.js for an example of how these attributes are configured.
    """

    def __init__(self, attrs=None, choices=(), filter_field=''):
        super().__init__(attrs, choices)
        self.filter_field = filter_field

    def build_attrs(self, base_attrs, extra_attrs=None):
        my_attrs = {
            'data-widget': 'filtered-select',
            'data-filter-field': self.filter_field,
        }
        if extra_attrs:
            my_attrs.update(extra_attrs)

        return super().build_attrs(base_attrs, my_attrs)

    def optgroups(self, name, value, attrs=None):
        # copy of Django's Select.optgroups, modified to accept filter_value as a
        # third item in the tuple and expose that as a data-filter-value attribute
        # on the final <option>
        groups = []
        has_selected = False

        for index, choice in enumerate(self.choices):
            try:
                (option_value, option_label, filter_value) = choice
            except ValueError:
                # *ChoiceField will still output blank options as a 2-tuple,
                # so need to handle that too
                (option_value, option_label) = choice
                filter_value = None

            if option_value is None:
                option_value = ''

            subgroup = []
            if isinstance(option_label, (list, tuple)):
                group_name = option_value
                subindex = 0
                choices = option_label
            else:
                group_name = None
                subindex = None
                choices = [(option_value, option_label)]
            groups.append((group_name, subgroup, index))

            for subvalue, sublabel in choices:
                selected = (
                    str(subvalue) in value
                    and (not has_selected or self.allow_multiple_selected)
                )
                has_selected |= selected

                subgroup.append(self.create_option(
                    name, subvalue, sublabel, selected, index, subindex=subindex,
                    filter_value=filter_value
                ))
                if subindex is not None:
                    subindex += 1
        return groups

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None, filter_value=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        if filter_value is not None:
            option['attrs']['data-filter-value'] = filter_value

        return option

    @property
    def media(self):
        return forms.Media(js=[
            versioned_static('wagtailadmin/js/filtered-select.js'),
        ])


class FilteredModelChoiceIterator(django_filters.fields.ModelChoiceIterator):
    """
    A variant of Django's ModelChoiceIterator that, instead of yielding (value, label) tuples,
    returns (value, label, filter_value) so that FilteredSelect can drop filter_value into
    the data-filter-value attribute.
    """
    def choice(self, obj):
        return (
            self.field.prepare_value(obj),
            self.field.label_from_instance(obj),
            self.field.get_filter_value(obj)
        )


class FilteredModelChoiceField(django_filters.fields.ModelChoiceField):
    widget = FilteredSelect
    iterator = FilteredModelChoiceIterator

    def __init__(self, *args, **kwargs):
        self.filter_accessor = kwargs.pop('filter_accessor')
        filter_field = kwargs.pop('filter_field')
        super().__init__(*args, **kwargs)
        self.widget.filter_field = filter_field

    def get_filter_value(self, obj):
        # filter_accessor identifies a property or method on the instances being listed here,
        # which gives us a queryset of related objects. Turn this queryset into a list of IDs
        # that will become the 'data-filter-value' used to filter this listing
        queryset = getattr(obj, self.filter_accessor)
        if callable(queryset):
            queryset = queryset()

        ids = queryset.values_list('pk', flat=True)
        return ','.join([str(id) for id in ids])


class FilteredModelChoiceFilter(django_filters.ModelChoiceFilter):
    field_class = FilteredModelChoiceField


class WagtailFilterSet(django_filters.FilterSet):

    @classmethod
    def filter_for_lookup(cls, field, lookup_type):
        filter_class, params = super().filter_for_lookup(field, lookup_type)

        if filter_class == django_filters.ChoiceFilter:
            params.setdefault('widget', ButtonSelect)
            params.setdefault('empty_label', _("All"))

        elif filter_class in [django_filters.DateFilter, django_filters.DateTimeFilter]:
            params.setdefault('widget', AdminDateInput)

        elif filter_class == django_filters.DateFromToRangeFilter:
            params.setdefault('widget', DateRangePickerWidget)

        elif filter_class == django_filters.BooleanFilter:
            params.setdefault('widget', BooleanButtonSelect)

        return filter_class, params


class LockedPagesReportFilterSet(WagtailFilterSet):
    locked_at = django_filters.DateFromToRangeFilter(widget=DateRangePickerWidget)

    class Meta:
        model = Page
        fields = ['locked_by', 'locked_at', 'live']


class WorkflowReportFilterSet(WagtailFilterSet):
    created_at = django_filters.DateFromToRangeFilter(label=_("Started at"), widget=DateRangePickerWidget)

    class Meta:
        model = WorkflowState
        fields = ['workflow', 'status', 'created_at']


class WorkflowTasksReportFilterSet(WagtailFilterSet):
    started_at = django_filters.DateFromToRangeFilter(label=_("Started at"), widget=DateRangePickerWidget)
    finished_at = django_filters.DateFromToRangeFilter(label=_("Completed at"), widget=DateRangePickerWidget)
    workflow = django_filters.ModelChoiceFilter(
        field_name='workflow_state__workflow', queryset=Workflow.objects.all(), label=_("Workflow")
    )

    # When a workflow is chosen in the 'id_workflow' selector, filter this list of tasks
    # to just the ones whose get_workflows() includes the selected workflow.
    task = FilteredModelChoiceFilter(
        queryset=Task.objects.all(), filter_field='id_workflow', filter_accessor='get_workflows'
    )

    class Meta:
        model = TaskState
        fields = ['workflow', 'task', 'status', 'started_at', 'finished_at']