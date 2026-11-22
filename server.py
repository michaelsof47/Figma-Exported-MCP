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
        raise RuntimeError("FIGMA_TOKEN doesn't available. Please to create .env file")
    return {"X-Figma-Token": token}

def get_json(client: httpx.Client, url: str, **params) -> dict:
    response = client.get(url, headers=headers(), params=params)

    if response.status_code == 429:
        retry = response.headers.get("Retry-After", "?")
        raise RuntimeError(f"Figma rate limit. Retry after{retry}s.")
    if response.status_code == 403:
        raise RuntimeError("Figma Token is invalid or no access in this file")
    if response.status_code == 404:
        raise RuntimeError("file_key or node_id not found")

    response.raise_for_status()
    return response.json()

@mcp.tool()
def export_to_pdf(
    file_key: str,
    node_ids: list[str],
    output_path: str = "",
    merge: bool = True,
) -> dict:

    if not node_ids:
        raise ValueError("node_ids must not be empty.")

    with httpx.Client(timeout=120, follow_redirects=True) as client:

        data = get_json(client, f"https://api.figma.com/v1/images/{file_key}", ids=",".join(node_ids), format="pdf")

        if data.get("err"):
            raise RuntimeError(f"Figma Error : {data["err"]}")

        urls: dict = data.get("images", {})

        pdf_bytes: dict[str, bytes] = {}
        failed: list[str] = []
        for nid in node_ids:
            url = urls.get(nid)
            if not url:
                failed.append(url)
                continue
            r = client.get(url)
            r.raise_for_status()
            pdf_bytes[nid] = r.content

        if not pdf_bytes:
            raise RuntimeError(f"No Node have been exported. Failed: {failed}")

        if merge:
            out = Path(output_path).expanduser() if output_path else DEFAULT_OUT_DIR / f"{file_key}.pdf"
            out.parent.mkdir(parents=True, exist_ok=True)

            writer = PdfWriter()
            for nid in node_ids:
                if nid in pdf_bytes:
                    writer.append(PdfReader(io.BytesIO(pdf_bytes[nid])))

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

