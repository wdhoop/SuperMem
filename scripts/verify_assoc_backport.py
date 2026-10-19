# -*- coding: utf-8 -*-
"""验证联想检索回写：默认行为不变 + expand 未配置静默回退 + 配置后联想句生成"""
import os, sys, json

MS_ROOT = os.environ.get("SUPERMEM_ROOT", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")))
EVAL_ROOT = os.environ.get("EVAL_ROOT", os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "results", "locomo_plus")))
sys.path.insert(0, MS_ROOT)

import config
config.EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
config.EMBED_MODEL_DIR = os.path.join(config.MODELS_DIR, "bge-small-en-v1.5")

from core import GraphStore, VectorSearch, CausalEngine, MemorySystem
from search_fusion import search_strong, gen_assoc_queries

class IsolatedMS(MemorySystem):
    def __init__(self, db_path, chroma_dir):
        self.graph = GraphStore(db_path=db_path)
        self.vector = VectorSearch(chroma_dir=chroma_dir)
        self.causal = CausalEngine(self.graph)

dbdir = os.path.join(EVAL_ROOT, "db_plus", "plus_0000")
ms = IsolatedMS(os.path.join(dbdir, "super_memory.db"), os.path.join(dbdir, "chroma_db"))

q = "The car keys are too expensive to replace, I think I will just use the spare for now."

# 1) 默认行为（ASSOC_ENABLED=False）
r1 = search_strong(ms, q, limit=8, project="plus_0000")
print("1) default: mode=%s results=%d assoc_queries=%s" % (r1["mode"], len(r1["results"]), r1.get("assoc_queries", [])))

# 2) expand=True 但未配置 -> 静默回退，应与默认一致
r2 = search_strong(ms, q, limit=8, project="plus_0000", expand=True)
same = r2["results"] == r1["results"] and r2["mode"] == r1["mode"]
print("2) expand unconfigured: mode=%s results=%d identical_to_default=%s" % (r2["mode"], len(r2["results"]), same))

# 3) 配置 assoc（DeepSeek chat 无思考模式）后 expand=True -> 生成联想句
config.ASSOC_ENABLED = True
config.ASSOC_ENDPOINT = "https://api.deepseek.com/v1"
config.ASSOC_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
config.ASSOC_MODEL = "deepseek-chat"
config.ASSOC_N = 4

aq = gen_assoc_queries(q)
print("3) assoc gen: n=%d -> %s" % (len(aq), json.dumps(aq[:2], ensure_ascii=False)[:300]))
r3 = search_strong(ms, q, limit=8, project="plus_0000", expand=True)
print("3) expand configured: mode=%s results=%d assoc_queries=%d" % (r3["mode"], len(r3["results"]), len(r3.get("assoc_queries", []))))
