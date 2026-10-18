from django.http import HttpResponseForbidden


class LocalOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.META.get('REMOTE_ADDR', '127.0.0.1') not in {'127.0.0.1', '::1'}:
            return HttpResponseForbidden('此版本仅允许本机访问。')
        response = self.get_response(request)
        response['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        # no-referrer makes browser form POSTs use Origin: null, which breaks CSRF.
        response['Referrer-Policy'] = 'same-origin'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
