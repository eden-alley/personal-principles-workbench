from django.urls import path, include
from django.views.static import serve
from django.conf import settings

urlpatterns = [path('', include('workbench.urls')), path('media/<path:path>', serve, {'document_root': settings.MEDIA_ROOT}), path('static/<path:path>', serve, {'document_root': settings.STATIC_ROOT})]
