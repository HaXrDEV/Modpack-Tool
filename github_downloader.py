import os
import aiohttp
import shutil
import tempfile
import zipfile
from typing import Optional, Dict


class AsyncGitHubDownloader:
    """Async GitHub repository downloader that fetches a branch/tag zipball and extracts selected folders."""

    GITHUB_API_URL = "https://api.github.com/repos"
    CHUNK_SIZE = 8192

    def __init__(self, repo_owner: str, repo_name: str, token: Optional[str] = None, branch: str = 'main'):
        """
        Initialize the AsyncGitHubDownloader with repository information.

        Parameters:
        - repo_owner: str, the owner of the repository.
        - repo_name: str, the name of the repository.
        - token: Optional[str], GitHub personal access token for authentication.
        - branch: str, the branch of the repository (default is 'main').
        """
        self.repo_owner = repo_owner
        self.repo_name = repo_name
        self.branch = branch
        self.headers = {
            'Accept': 'application/vnd.github.v3+json'
        }
        if token:
            self.headers['Authorization'] = f'token {token}'

    async def _download_archive(self, session: aiohttp.ClientSession, archive_path: str):
        """
        Download repository archive (zipball) for the configured branch/tag.
        """
        archive_url = f"{self.GITHUB_API_URL}/{self.repo_owner}/{self.repo_name}/zipball/{self.branch}"
        async with session.get(archive_url, headers=self.headers, allow_redirects=True) as response:
            if response.status == 200:
                with open(archive_path, 'wb') as archive_file:
                    async for chunk in response.content.iter_chunked(self.CHUNK_SIZE):
                        archive_file.write(chunk)
                return
            elif response.status == 401:
                raise RuntimeError("Authentication failed. Please check your GitHub token.")
            elif response.status == 403:
                raise RuntimeError("Rate limit exceeded or repository access denied.")
            elif response.status == 404:
                raise RuntimeError("Repository or branch/tag not found.")
            else:
                raise RuntimeError(f"Failed to download repository archive: {response.status}")

    @staticmethod
    def _normalize_repo_path(path: str) -> str:
        return str(path).strip().strip("/\\").replace("\\", "/")

    @staticmethod
    def _find_extracted_repo_root(extract_dir: str) -> str:
        entries = [os.path.join(extract_dir, item) for item in os.listdir(extract_dir)]
        dir_entries = [entry for entry in entries if os.path.isdir(entry)]
        if len(dir_entries) == 1:
            return dir_entries[0]
        raise RuntimeError("Unable to determine extracted repository root directory.")

    async def download_repo_snapshot(self, destination: str, folder_mappings: Dict[str, str]):
        """
        Download the full repository snapshot once, then move selected folders into destination.

        Parameters:
        - destination: str, local destination root.
        - folder_mappings: Dict[str, str], mapping of repo-relative folder path -> destination-relative folder path.
        """
        os.makedirs(destination, exist_ok=True)

        temp_root = tempfile.mkdtemp(prefix=f"{self.repo_name}-{self.branch}-")
        archive_path = os.path.join(temp_root, "repo_snapshot.zip")
        extract_dir = os.path.join(temp_root, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        try:
            timeout = aiohttp.ClientTimeout(total=300)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await self._download_archive(session, archive_path)

            with zipfile.ZipFile(archive_path, 'r') as archive:
                archive.extractall(extract_dir)

            repo_root = self._find_extracted_repo_root(extract_dir)

            for source_folder, destination_folder in folder_mappings.items():
                source_normalized = self._normalize_repo_path(source_folder)
                destination_normalized = self._normalize_repo_path(destination_folder)

                source_parts = [part for part in source_normalized.split("/") if part]
                source_path = os.path.join(repo_root, *source_parts)
                target_path = os.path.join(destination, destination_normalized)

                if os.path.isdir(target_path):
                    shutil.rmtree(target_path)
                elif os.path.isfile(target_path):
                    os.remove(target_path)

                if os.path.isdir(source_path):
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    shutil.move(source_path, target_path)
                    print(f"Moved '{source_folder}' -> '{destination_folder}'")
                else:
                    # Keep expected folder layout stable even when source folder does not exist for a tag.
                    os.makedirs(target_path, exist_ok=True)
                    print(f"Missing '{source_folder}' in snapshot. Created empty '{destination_folder}'.")

            print("Repository snapshot downloaded and processed successfully.")
        finally:
            # Remove full downloaded snapshot + all files that are not needed after moving selected folders.
            shutil.rmtree(temp_root, ignore_errors=True)
