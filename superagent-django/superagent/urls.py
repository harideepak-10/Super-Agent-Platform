from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/auth/", include("apps.authentication.urls")),
    path("api/v1/tasks/", include("apps.tasks.urls")),
    path("api/v1/agents/", include("apps.agents.urls")),
    path("api/v1/approvals/", include("apps.approvals.urls")),
    path("api/v1/integrations/", include("apps.integrations.urls")),
    path("api/v1/audit/", include("apps.audit.urls")),
    path("api/v1/costs/", include("apps.costs.urls")),
    path("api/v1/team/", include("apps.team.urls")),
    path("api/v1/notifications/", include("apps.notifications.urls")),
    path("api/v1/search/", include("apps.search.urls")),
    path("api/v1/profile/", include("apps.authentication.profile_urls")),
    path("api/v1/memory/", include("apps.memory.urls")),
    path("api/v1/dashboard/", include("apps.dashboard.urls")),
    path("api/v1/quick-tasks/", include("apps.quick_tasks.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
