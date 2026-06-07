"""URL mirror substitution for domestic users.

Provides a single function `maybe_mirror_url()` that replaces known CDN
endpoints (HuggingFace, GitHub) with user-configured mirrors.
Called at download time by `utils.download_util` and `scripts.check_update`.
"""


def maybe_mirror_url(
    url: str,
    hf_endpoint: str = "",
    github_mirror: str = "",
) -> str:
    """Replace known CDN prefixes with configured mirrors.

    Args:
        url: Original download URL.
        hf_endpoint: HF mirror base (e.g. ``https://hf-mirror.com``).
        github_mirror: GitHub mirror base (e.g. ``https://gitclone.com``).

    Returns:
        URL with the first matching prefix replaced, or the original URL
        unchanged if no mirror is configured or no prefix matches.
    """
    replacements = []
    if hf_endpoint:
        replacements.append(("https://huggingface.co", hf_endpoint.rstrip("/")))
    if github_mirror:
        replacements.append(("https://github.com", github_mirror.rstrip("/")))

    if not replacements:
        return url

    for original, mirror in replacements:
        if url.startswith(original):
            return url.replace(original, mirror, 1)
    return url


def patch_hf_env(hf_endpoint: str):
    """Set ``HF_ENDPOINT`` so ``huggingface_hub`` uses the configured mirror.

    Call early at startup after config is loaded.
    """
    if hf_endpoint:
        import os

        os.environ["HF_ENDPOINT"] = hf_endpoint.rstrip("/")
