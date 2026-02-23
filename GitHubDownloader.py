import os
import aiohttp
import asyncio
from typing import Optional


class AsyncGitHubDownloader:
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

    async def _fetch(self, session: aiohttp.ClientSession, url: str):
        """
        Asynchronously fetch data from the given URL.
        
        Parameters:
        - session: aiohttp.ClientSession, the session to use for making requests.
        - url: str, the URL to fetch.

        Returns:
        - JSON response data.
        """
        async with session.get(url, headers=self.headers) as response:
            if response.status == 200:
                return await response.json()
            elif response.status == 401:
                raise RuntimeError("Authentication failed. Please check your GitHub token.")
            elif response.status == 403:
                raise RuntimeError("Rate limit exceeded or repository access denied.")
            elif response.status == 404:
                raise RuntimeError("Repository or path not found.")
            else:
                raise RuntimeError(f"Failed to fetch data: {response.status}")

    async def _download_file(self, session: aiohttp.ClientSession, url: str, dest_folder: str, filename: str):
        """
        Asynchronously download a single file and save it to the destination folder.
        
        Parameters:
        - session: aiohttp.ClientSession, the session to use for downloading.
        - url: str, the file download URL.
        - dest_folder: str, the local folder where the file will be saved.
        - filename: str, the name of the file.
        """
        file_path = os.path.join(dest_folder, filename)
        os.makedirs(dest_folder, exist_ok=True)
        async with session.get(url, headers=self.headers) as response:
            if response.status == 200:
                with open(file_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(self.CHUNK_SIZE):
                        f.write(chunk)
                print(f"Downloaded {filename}")
            else:
                print(f"Failed to download {filename}: {response.status}")

    async def _get_folder_contents(self, session: aiohttp.ClientSession, folder_path: str):
        """
        Asynchronously fetch the contents of a folder in the repository.
        
        Parameters:
        - session: aiohttp.ClientSession, the session to use for making requests.
        - folder_path: str, the folder path inside the repository.

        Returns:
        - List of files and folders in the given path.
        """
        api_url = f"{self.GITHUB_API_URL}/{self.repo_owner}/{self.repo_name}/contents/{folder_path}?ref={self.branch}"
        return await self._fetch(session, api_url)

    async def _download_folder_recursive(self, session: aiohttp.ClientSession, folder_path: str, dest_folder: str):
        try:
            folder_contents = await self._get_folder_contents(session, folder_path)
        except Exception as e:
            print(f"Error: {str(e)}")
            return

        tasks = []
        for item in folder_contents:
            item_type = str(item.get("type", "")).lower()
            if item_type == "file":
                download_url = item.get("download_url")
                filename = item.get("name")
                if download_url and filename:
                    tasks.append(self._download_file(session, download_url, dest_folder, filename))
            elif item_type == "dir":
                nested_path = item.get("path")
                nested_name = item.get("name")
                if not nested_path or not nested_name:
                    continue
                nested_dest = os.path.join(dest_folder, nested_name)
                os.makedirs(nested_dest, exist_ok=True)
                tasks.append(self._download_folder_recursive(session, nested_path, nested_dest))

        if tasks:
            await asyncio.gather(*tasks)

    async def download_folder(self, folder_path: str, dest_folder: str, recursive: bool = False):
        """
        Asynchronously download all files from a specific folder in the repository.
        
        Parameters:
        - folder_path: str, the folder path inside the repository.
        - dest_folder: str, the local folder where files will be saved.
        - recursive: bool, download files in nested directories recursively.
        """
        os.makedirs(dest_folder, exist_ok=True)

        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if recursive:
                await self._download_folder_recursive(session, folder_path, dest_folder)
            else:
                try:
                    folder_contents = await self._get_folder_contents(session, folder_path)
                except Exception as e:
                    print(f"Error: {str(e)}")
                    return

                tasks = []
                for item in folder_contents:
                    if item.get("type") == "file":
                        download_url = item.get("download_url")
                        filename = item.get("name")
                        if download_url and filename:
                            tasks.append(self._download_file(session, download_url, dest_folder, filename))

                if tasks:
                    await asyncio.gather(*tasks)
        print("All files downloaded successfully.")

# Example usage:
# async def main():
#     # Without authentication (for public repositories)
#     downloader = AsyncGitHubDownloader('octocat', 'Hello-World')
#     await downloader.download_folder('path/to/folder', './local_folder')
#
#     # With authentication (for private repositories or to avoid rate limiting)
#     token = "your_github_personal_access_token"
#     authenticated_downloader = AsyncGitHubDownloader('octocat', 'Hello-World', token=token)
#     await authenticated_downloader.download_folder('path/to/folder', './local_folder')
#
# asyncio.run(main())
