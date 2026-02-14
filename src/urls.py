"""
URL configuration for crawler project.
"""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path('admin/', admin.site.urls),
]

# Customize admin site
admin.site.site_header = "Trend Crawler Administration"
admin.site.site_title = "Trend Crawler"
admin.site.index_title = "Culture-Flexible Trend Crawler"
