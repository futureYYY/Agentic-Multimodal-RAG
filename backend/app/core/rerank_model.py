"""
Rerank 模型加载器 (单例模式)
"""
from typing import Optional, Any, List, Dict

class RerankModelLoader:
    _instance = None
    _model = None
    _tokenizer = None
    _current_model_path: Optional[str] = None
    _device = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RerankModelLoader, cls).__new__(cls)
        return cls._instance

    @property
    def device(self):
        if self._device is None:
            import torch
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        return self._device

    def load_model(self, model_path: str, max_len: int = 8196):
        """
        加载模型。如果模型已经加载且路径一致，则跳过。
        """
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        if self._model is not None and self._current_model_path == model_path:
            print(f"Rerank model {model_path} already loaded.")
            return

        print(f"Loading rerank model from {model_path} to {self.device}...")
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True, trust_remote_code=True)
            dtype = torch.float16 if self.device == "cuda" else torch.float32
            self._model = AutoModelForSequenceClassification.from_pretrained(
                model_path, 
                local_files_only=True, 
                dtype=dtype,
                trust_remote_code=True
            )
            self._model.eval().to(self.device)
            self._current_model_path = model_path
            self._max_len = max_len
            print("Rerank model loaded successfully!")
        except Exception as e:
            print(f"Failed to load rerank model: {e}")
            raise e

    def predict(self, query: str, passages: List[str], model_path: str, max_len: int = 8196) -> List[Dict[str, Any]]:
        """
        执行推理
        :param query: 查询
        :param passages: 候选文档列表
        :param model_path: 模型路径 (用于检查是否需要重新加载)
        :param max_len: 最大上下文长度
        :return: 排序结果 [{"index": i, "score": float, "text": str}]
        """
        import torch
        
        # 确保模型已加载
        self.load_model(model_path, max_len)

        if not self._model or not self._tokenizer:
            raise RuntimeError("Rerank model not initialized")

        try:
            # 构建输入 (参考用户示例代码格式: "query: ... document: ...")
            # input_texts = [f"query: {query} document: {doc}" for doc in passages]
            
            # 注意：虽然BGE标准用法通常是pairs，但为了与用户验证过的代码保持一致，这里支持两种模式
            # 或者直接采用用户提供的 formatted string 方式
            
            input_texts = [f"query: {query} document: {doc}" for doc in passages]
            
            with torch.no_grad():
                inputs = self._tokenizer(
                    input_texts, 
                    padding=True, 
                    truncation=True, 
                    return_tensors='pt', 
                    max_length=max_len
                ).to(self.device)
                
                scores = self._model(**inputs, return_dict=True).logits.view(-1,).float()
                # 使用 sigmoid 归一化到 0-1
                scores = torch.sigmoid(scores).cpu().numpy().tolist()

            # 构造结果
            results = []
            for i, score in enumerate(scores):
                results.append({
                    "index": i,
                    "score": float(score),
                    "text": passages[i]
                })
            
            # 按分数降序排列
            results.sort(key=lambda x: x["score"], reverse=True)
            return results

        except Exception as e:
            print(f"Rerank inference failed: {e}")
            raise e
