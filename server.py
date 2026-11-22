import os
import io
from pathlib import Path
import httpx

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from pypdf import PdfWriter
from pypdf import PdfReader

load_dotenv()

DEFAULT_OUT_DIR = Path(os.environ.get("FIGMA_OUT_DIR", "~/figma_exports")).expanduser()

mcp = MCPServer("figma-exported")

def headers():
    token = os.environ.get("FIGMA_TOKEN")
    if not token:
        raise RuntimeError("FIGMA_TOKEN is not available. Please create a .env file with FIGMA_TOKEN.")
    return {"X-Figma-Token": token}

def get_json(client: httpx.Client, url: str, **params) -> dict:
    response = client.get(url, headers=headers(), params=params)

    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "?")
        raise RuntimeError(f"Figma rate limit. Retry after {retry}s.")
    if response.status_code == 403:
        raise RuntimeError("Figma Token is invalid or has no access to this file.")
    if response.status_code == 404:
        raise RuntimeError("file_key or node_id not found.")

    response.raise_for_status()
    return response.json()

@mcp.tool()
def get_file_info(file_key: str) -> dict:
    """Get metadata and top-level pages/frames of a Figma document."""
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        data = get_json(client, f"https://api.figma.com/v1/files/{file_key}?depth=2")
        document = data.get("document", {})
        pages = []
        for page in document.get("children", []):
            frames = [{"id": child.get("id"), "name": child.get("name"), "type": child.get("type")} for child in page.get("children", [])]
            pages.append({
                "id": page.get("id"),
                "name": page.get("name"),
                "type": page.get("type"),
                "frames": frames
            })
        return {
            "name": data.get("name"),
            "lastModified": data.get("lastModified"),
            "pages": pages
        }

@mcp.tool()
def export_to_pdf(
    file_key: str,
    node_ids: list[str],
    output_path: str = "",
    merge: bool = True,
) -> dict:
    """Export specified Figma nodes (frames/pages) to PDF files."""
    if not node_ids:
        raise ValueError("node_ids must not be empty.")

    # Normalize node IDs: Figma URLs often use '-' instead of ':'
    normalized_ids = [nid.replace("-", ":") if ("-" in nid and ":" not in nid) else nid for nid in node_ids]

    with httpx.Client(timeout=120, follow_redirects=True) as client:
        data = get_json(client, f"https://api.figma.com/v1/images/{file_key}", ids=",".join(normalized_ids), format="pdf")

        if data.get("err"):
            raise RuntimeError(f"Figma Error : {data['err']}")

        urls: dict = data.get("images", {})

        pdf_bytes: dict[str, bytes] = {}
        failed: list[str] = []
        for orig_id, norm_id in zip(node_ids, normalized_ids):
            url = urls.get(norm_id) or urls.get(orig_id)
            if not url:
                failed.append(orig_id)
                continue
            r = client.get(url)
            r.raise_for_status()
            pdf_bytes[norm_id] = r.content

        if not pdf_bytes:
            raise RuntimeError(f"No nodes could be exported. Failed node IDs: {failed}")

        if merge:
            out = Path(output_path).expanduser() if output_path else DEFAULT_OUT_DIR / f"{file_key}.pdf"
            out.parent.mkdir(parents=True, exist_ok=True)

            writer = PdfWriter()
            for norm_id in normalized_ids:
                if norm_id in pdf_bytes:
                    writer.append(PdfReader(io.BytesIO(pdf_bytes[norm_id])))

            with open(out, "wb") as f:
                writer.write(f)
            files = [str(out)]
        else:
            out = Path(output_path).expanduser() if output_path else DEFAULT_OUT_DIR
            out.mkdir(parents=True, exist_ok=True)
            files = []
            for nid, content in pdf_bytes.items():
                p = out / f"{file_key}_{nid.replace(':', '-')}.pdf"
                p.write_bytes(content)
                files.append(str(p))

    return {"files": files, "exported": list(pdf_bytes), "failed": failed}

if __name__ == "__main__":
    mcp.run()
