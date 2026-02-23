import requests

def check_tag_exists(repo_owner, repo_name, tag_name, github_token=None):
    """
    Checks if a specific tag exists on a GitHub repository.

    :param repo_owner: The owner of the repository (e.g., 'octocat')
    :param repo_name: The name of the repository (e.g., 'Hello-World')
    :param tag_name: The name of the tag to check (e.g., 'v1.0.0')
    :param github_token: Optional GitHub token for authentication
    :return: True if the tag exists, False otherwise
    """
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/git/refs/tags/{tag_name}"
    
    headers = {}
    if github_token:
        headers['Authorization'] = f'token {github_token}'
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
    except requests.RequestException as ex:
        raise RuntimeError(f"Failed to query GitHub tag '{tag_name}': {ex}") from ex

    if response.status_code == 200:
        return True  # Tag exists
    elif response.status_code == 404:
        return False  # Tag does not exist
    else:
        # Handle other unexpected status codes
        raise RuntimeError(f"Unexpected error: {response.status_code} - {response.text}")
