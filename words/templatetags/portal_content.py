from django import template


register = template.Library()


@register.filter
def announcement_html(body):
    from accounts.announcement_content import render_announcement

    return render_announcement(body)
