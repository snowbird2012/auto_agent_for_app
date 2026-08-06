"""Text decoding, paragraph-aware chunking, indexing and retrieval."""

from pathlib import Path

from services.model_test_client import ModelTestClient
from storage.knowledge_repository import KnowledgeRepository
from storage.settings_repository import SettingsRepository


class KnowledgeService:
    def __init__(self, repository: KnowledgeRepository, settings: SettingsRepository) -> None:
        self.repository = repository; self.settings = settings

    @staticmethod
    def read_text(path: Path) -> tuple[str, str]:
        data = path.read_bytes()
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try: return data.decode(encoding), encoding
            except UnicodeDecodeError: pass
        raise ValueError("文本编码无法识别，请转换为 UTF-8 或 GB18030")

    @staticmethod
    def split_text(text: str, size: int, overlap: int) -> list[str]:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        paragraphs = [" ".join(part.split()) for part in normalized.split("\n\n") if part.strip()]
        chunks: list[str] = []
        buffer = ""
        for paragraph in paragraphs:
            pieces = [paragraph[i:i+size] for i in range(0, len(paragraph), max(1, size-overlap))] if len(paragraph) > size else [paragraph]
            for piece in pieces:
                candidate = (buffer + "\n\n" + piece).strip() if buffer else piece
                if len(candidate) <= size: buffer = candidate
                else:
                    if buffer: chunks.append(buffer)
                    buffer = piece
        if buffer: chunks.append(buffer)
        return chunks

    def _runtime(self, base: dict) -> tuple[dict, dict, dict]:
        model = self.settings.get_model(int(base["embedding_model_id"]))
        if not model: raise ValueError("知识库配置的向量模型不存在")
        provider = self.settings.get_provider(int(model["provider_id"]), reveal_key=True)
        if not provider: raise ValueError("向量模型厂家不存在")
        return provider, model, self.settings.get_proxy_settings(reveal_password=True)

    def index_file(self, base_id: int, file_path: str | Path) -> int:
        base = self.repository.get_base(base_id)
        if not base: raise ValueError("知识库不存在")
        path = Path(file_path)
        if path.suffix.lower() != ".txt" or not path.is_file(): raise ValueError("请选择有效的 TXT 文件")
        text, encoding = self.read_text(path)
        chunks = self.split_text(text, int(base["chunk_size"]), int(base["chunk_overlap"]))
        if not chunks: raise ValueError("文件中没有可索引的文本")
        provider, model, proxy = self._runtime(base)
        vectors: list[list[float]] = []
        client = ModelTestClient()
        for start in range(0, len(chunks), 16):
            vectors.extend(client.create_embeddings(provider, model, chunks[start:start+16], proxy))
        self.repository.replace_document(base_id, path, encoding, chunks, vectors, int(model["id"]))
        return len(chunks)

    def search(self, base_id: int, query: str) -> list[dict]:
        base = self.repository.get_base(base_id)
        if not base: raise ValueError("知识库不存在")
        provider, model, proxy = self._runtime(base)
        vector = ModelTestClient().create_embeddings(provider, model, [query], proxy)[0]
        return self.repository.search(base_id, vector, int(base["top_k"]), float(base["min_score"]))

    def search_many(self, base_ids: list[int], query: str, max_results: int = 12) -> dict:
        """Search multiple bases, reusing one query embedding per vector model."""
        groups: dict[int, list[dict]] = {}
        errors: list[dict] = []
        for base_id in dict.fromkeys(int(value) for value in base_ids):
            base = self.repository.get_base(base_id)
            if not base:
                errors.append({"knowledge_base_id":base_id,"name":f"#{base_id}","error":"知识库不存在"})
                continue
            if not base["enabled"]:
                errors.append({"knowledge_base_id":base_id,"name":base["name"],"error":"知识库已停用"})
                continue
            groups.setdefault(int(base["embedding_model_id"]), []).append(base)

        matches: list[dict] = []
        client = ModelTestClient()
        for bases in groups.values():
            try:
                provider, model, proxy = self._runtime(bases[0])
                vector = client.create_embeddings(provider, model, [query], proxy)[0]
            except Exception as error:
                for base in bases:
                    errors.append({"knowledge_base_id":base["id"],"name":base["name"],"error":str(error)})
                continue
            for base in bases:
                try:
                    rows = self.repository.search(
                        int(base["id"]), vector, int(base["top_k"]), float(base["min_score"])
                    )
                    for row in rows:
                        item=dict(row); item["knowledge_base_id"]=int(base["id"]); item["knowledge_base_name"]=base["name"]
                        matches.append(item)
                except Exception as error:
                    errors.append({"knowledge_base_id":base["id"],"name":base["name"],"error":str(error)})

        unique: dict[str, dict] = {}
        for item in sorted(matches,key=lambda value:value["score"],reverse=True):
            key=" ".join(str(item["content"]).split())
            if key not in unique:unique[key]=item
        return {"results":list(unique.values())[:max(1,int(max_results))],"errors":errors}
