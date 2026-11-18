"""Static files under ASGI (api/static.py). WhiteNoise's middleware is synchronous and returns
a FileResponse over a plain file, which Django can only drain through a thread with a warning. This
subclass is async and streams the file through an async generator that reads it in a worker thread."""
import asyncio

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from whitenoise.middleware import WhiteNoiseFileResponse, WhiteNoiseMiddleware

CHUNK_SIZE = 64 * 1024


async def _read_chunks(file):
    """An async iterator over the file; empty for a HEAD or 304 response, which has no file."""
    if file is None:
        return
    while chunk := await asyncio.to_thread(file.read, CHUNK_SIZE):
        yield chunk


class AsyncWhiteNoiseMiddleware(WhiteNoiseMiddleware):
    sync_capable = False
    async_capable = True

    def __init__(self, get_response, *args, **kwargs):
        if not iscoroutinefunction(get_response):
            raise TypeError("AsyncWhiteNoiseMiddleware needs an asynchronous middleware chain.")
        super().__init__(get_response, *args, **kwargs)
        markcoroutinefunction(self)

    async def __call__(self, request):
        static_file = self.files.get(request.path_info)
        if static_file is None:
            return await self.get_response(request)
        return await asyncio.to_thread(self._serve, static_file, request)

    @staticmethod
    def _serve(static_file, request):
        """Runs in a thread: get_response opens the file and may stat it."""
        response = static_file.get_response(request.method, request.META)
        file = response.file
        http_response = WhiteNoiseFileResponse(_read_chunks(file), status=int(response.status))
        if file:
            http_response._resource_closers.append(file.close)
        del http_response["content-type"]
        for key, value in response.headers:
            http_response[key] = value
        return http_response
