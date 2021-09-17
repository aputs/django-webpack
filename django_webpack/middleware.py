from pathlib import Path
from django.conf import settings
from django.http.request import HttpRequest
from django.views.static import serve

from .webpack import staticfiles_matcher, staticfiles_prefix


class WebpackStaticMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        static_file = staticfiles_matcher.findall(request.path)
        if static_file:
            return serve(
                request,
                static_file[0],
                document_root=Path(settings.PUBLIC_ROOT) / staticfiles_prefix,
                show_indexes=True,
            )

        return self.get_response(request)
