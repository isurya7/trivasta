from django.contrib import admin
from django.utils.html import format_html
from .models import Trip, Itinerary, Review, ReviewReply, ReviewHelpfulVote


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display  = (
        'destination', 'user', 'days', 'budget', 'budget_type',
        'num_people', 'travel_type', 'travel_mode', 'origin', 'created_at'
    )
    list_filter   = ('travel_type', 'travel_mode', 'budget_type')
    search_fields = ('destination', 'origin', 'user__username')
    ordering      = ('-created_at',)
    readonly_fields = ('created_at',)
    fieldsets = (
        ('Trip Info', {
            'fields': ('user', 'destination', 'origin', 'travel_type', 'travel_mode')
        }),
        ('Duration & People', {
            'fields': ('days', 'start_date', 'end_date', 'num_people')
        }),
        ('Budget', {
            'fields': ('budget', 'budget_type')
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )


@admin.register(Itinerary)
class ItineraryAdmin(admin.ModelAdmin):
    list_display    = ('trip', 'destination_preview', 'estimated_cost', 'created_at')
    search_fields   = ('trip__destination', 'trip__user__username')
    ordering        = ('-created_at',)
    readonly_fields = ('created_at',)

    def destination_preview(self, obj):
        return obj.trip.destination
    destination_preview.short_description = 'Destination'


class ReviewReplyInline(admin.StackedInline):
    model       = ReviewReply
    extra       = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display    = ('id', 'reviewer', 'agency', 'star_display', 'title', 'helpful_votes', 'is_visible', 'created_at')
    list_filter     = ('overall_rating', 'is_visible', 'agency')
    search_fields   = ('reviewer__email', 'agency__name', 'title', 'body')
    readonly_fields = ('created_at', 'updated_at', 'helpful_votes')
    list_editable   = ('is_visible',)
    inlines         = [ReviewReplyInline]
    ordering        = ('-created_at',)

    @admin.display(description="Rating")
    def star_display(self, obj):
        stars = "★" * obj.overall_rating + "☆" * (5 - obj.overall_rating)
        color = (
            "#D4762A" if obj.overall_rating >= 4
            else ("#E57373" if obj.overall_rating <= 2 else "#F9A825")
        )
        return format_html('<span style="color:{};font-size:16px;">{}</span>', color, stars)


@admin.register(ReviewReply)
class ReviewReplyAdmin(admin.ModelAdmin):
    list_display    = ('id', 'review', 'replied_by', 'created_at')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(ReviewHelpfulVote)
class ReviewHelpfulVoteAdmin(admin.ModelAdmin):
    list_display    = ('review', 'voter', 'created_at')
    readonly_fields = ('created_at',)