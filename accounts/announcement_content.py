from django.utils.safestring import mark_safe
from mistune import HTMLRenderer, create_markdown


class AnnouncementRenderer(HTMLRenderer):
    def image(self, text, url, title=None):
        # Notices must not pull images from third-party origins.
        return text


_markdown = create_markdown(renderer=AnnouncementRenderer(escape=True), plugins=['strikethrough', 'table'])


def render_announcement(body):
    return mark_safe(_markdown(body))
