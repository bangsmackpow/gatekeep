import logging
from typing import Optional
from azure.storage.blob.aio import BlobServiceClient, ContainerClient
from azure.core.exceptions import ResourceNotFoundError
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class AzureBlobStorage:
    def __init__(self, connection_string: str, container_name: str):
        self.connection_string = connection_string
        self.container_name = container_name
        self._blob_service: Optional[BlobServiceClient] = None
        self._container: Optional[ContainerClient] = None
        self._account_name: Optional[str] = None

    @property
    def account_name(self) -> str:
        if not self._account_name:
            parts = self.connection_string.split(";")
            for part in parts:
                if part.startswith("AccountName="):
                    self._account_name = part.split("=", 1)[1]
                    break
        return self._account_name or "unknown"

    async def _get_container(self) -> ContainerClient:
        if not self._container:
            self._blob_service = BlobServiceClient.from_connection_string(self.connection_string)
            self._container = self._blob_service.get_container_client(self.container_name)
            try:
                await self._container.create_container()
            except Exception:
                pass
        return self._container

    async def upload_blob(self, blob_name: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        container = await self._get_container()
        blob_client = container.get_blob_client(blob_name)
        await blob_client.upload_blob(data, overwrite=True, content_type=content_type)
        return blob_client.url

    async def download_blob(self, blob_name: str) -> bytes:
        container = await self._get_container()
        blob_client = container.get_blob_client(blob_name)
        try:
            download = await blob_client.download_blob()
            return await download.readall()
        except ResourceNotFoundError:
            logger.error(f"Blob not found: {blob_name}")
            raise

    async def delete_blob(self, blob_name: str):
        container = await self._get_container()
        blob_client = container.get_blob_client(blob_name)
        await blob_client.delete_blob()

    async def blob_exists(self, blob_name: str) -> bool:
        container = await self._get_container()
        blob_client = container.get_blob_client(blob_name)
        return await blob_client.exists()

    async def generate_sas_url(self, blob_name: str, expiry_hours: int = 1) -> str:
        from azure.storage.blob import generate_blob_sas, BlobSasPermissions
        container = await self._get_container()
        sas_token = generate_blob_sas(
            account_name=self.account_name,
            container_name=self.container_name,
            blob_name=blob_name,
            account_key=self._extract_account_key(),
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
        )
        return f"https://{self.account_name}.blob.core.windows.net/{self.container_name}/{blob_name}?{sas_token}"

    def _extract_account_key(self) -> str:
        parts = self.connection_string.split(";")
        for part in parts:
            if part.startswith("AccountKey="):
                return part.split("=", 1)[1]
        raise ValueError("AccountKey not found in connection string")

    async def close(self):
        if self._blob_service:
            await self._blob_service.close()
