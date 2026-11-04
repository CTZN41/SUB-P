"""Bound legacy multipart reception before FastAPI spools the complete body."""
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException


class BodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(413, detail={"code": "FILE_LIMIT", "message": "영상 파일의 허용 용량을 초과했습니다."})


class UploadBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].rstrip("/") != "/api/analyses":
            await self.app(scope, receive, send)
            return
        limit = scope["app"].state.settings.max_video_bytes + 64 * 1024
        headers = dict(scope["headers"])
        length = headers.get(b"content-length", b"")
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                raise BodyTooLarge
            return message

        try:
            if length.isdigit() and int(length) > limit:
                raise BodyTooLarge
            await self.app(scope, limited_receive, send)
        except BodyTooLarge:
            response = JSONResponse({"detail": {"code": "FILE_LIMIT", "message": "영상 파일의 허용 용량을 초과했습니다."}}, status_code=413)
            await response(scope, receive, send)
