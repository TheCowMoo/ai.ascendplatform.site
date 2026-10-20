"""
Image generation tool (stage S5).

Backed by Gemini's native image model via the ``google-genai`` SDK. The model id
is configuration, not code, because Google keeps renaming these
(``gemini-2.5-flash-image``, ``gemini-nano-banana-*``, Imagen). In dry-run nothing
is generated: the intended model, prompt and path are returned instead.
"""

import os

import actions
import settings
from tools import policy
from tools import register

# Structural: read once, since it decides where output lands.
ARTIFACTS_DIR = (os.environ.get("ARTIFACTS_DIR") or "artifacts").rstrip("/\\")


# Read per call, so the Settings tab applies without a restart.
def model():
    return settings.get("GEMINI_IMAGE_MODEL") or "gemini-2.5-flash-image"


def api_key():
    return settings.get("GEMINI_API_KEY")

ALLOWED_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _safe_name(filename):
    name = os.path.basename(filename or "image.png").replace("..", "_").strip() or "image.png"
    if not name.lower().endswith(ALLOWED_SUFFIXES):
        name += ".png"
    return name


def save_inline_image(response, path):
    """Write the first inline image part to disk. Returns True when one was found."""
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline else None
            if not data:
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(data if isinstance(data, bytes) else bytes(data))
            return True
    return False


def _build_generate_image(emit, job_id=None):
    async def generate_image(prompt: str, filename: str = "image.png") -> str:
        """Generate an image from a text prompt and save it as an artifact."""
        if not (prompt or "").strip():
            return "A prompt is required."
        path = os.path.join(ARTIFACTS_DIR, "images", "job-%s" % (job_id or "manual"), _safe_name(filename))

        decision = policy.check_path(path, ARTIFACTS_DIR)
        if not decision.allowed:
            return "Refused to write the image: %s" % decision.reason

        # Generating into the artifacts directory is internal work, like a download, so
        # it is not approval-gated. Approvals are for actions that leave the box:
        # sending mail, publishing a page, provisioning a server.
        authorisation = {"dry_run": actions.is_dry_run()}
        if not authorisation["allowed"]:
            return "Not generated: %s" % authorisation["reason"]
        if authorisation["dry_run"]:
            return "[dry-run] would generate an image with %s -> %s (prompt: %s)" % (
                model(),
                path,
                prompt[:120],
            )
        if not api_key():
            return "GEMINI_API_KEY is not set, so no image can be generated."

        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - google-genai is a hard dependency
            return "google-genai is not installed (%s)" % exc

        try:
            client = genai.Client()
            response = client.models.generate_content(model=model(), contents=[prompt])
        except Exception as exc:
            return "Image generation failed (%s: %s)" % (type(exc).__name__, exc)

        if not save_inline_image(response, path):
            return "The model returned no image data."
        return "Generated %s" % path

    return generate_image


register(
    "generate_image",
    "Generate an image from a text prompt and save it as an artifact.",
    _build_generate_image,
)
