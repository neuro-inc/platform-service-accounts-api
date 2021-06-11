from typing import Optional

from neuro_auth_client import AuthClient as BaseAuthCLient


class AuthClient(BaseAuthCLient):
    async def delete_user(self, name: str, token: Optional[str] = None) -> None:
        path = self._get_user_path(name)
        headers = self._generate_headers(token)
        async with self._request("DELETE", path, headers=headers):
            pass  # use context manager to release response earlier
