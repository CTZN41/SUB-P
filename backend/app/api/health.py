from fastapi import APIRouter, Request


router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health_check(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "service": "basketball-shot-analysis",
        "environment": settings.app_env,
        "storage": {
            "uploads_ready": settings.upload_path.is_dir(),
            "work_ready": settings.work_path.is_dir(),
            "database_ready": settings.data_path.is_dir(),
        },
    }
