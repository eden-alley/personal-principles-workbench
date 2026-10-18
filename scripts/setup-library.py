"""One-time public model download; runtime uses the local cache only."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.services import semantic

model = semantic.load(download=True)
texts = ['独处有助于独立思考，我想保留不受打扰的时间。',
         '我重视自主选择，独处和高质量社交都可以帮助成长。',
         '主动参加朋友聚会让我收获很多，交流让我很有精神。',
         'Python 的列表推导式可以简洁地创建列表。',
         '投资前先考虑风险，不追逐短期收益。',
         '没有足够证据时，不急着下定论。']
vectors = semantic.encode(texts)
result=[]
for query in ['我是不是太在意合群了？','朋友约我出去，我其实更想自己待着。','Python怎么写列表推导式？','晚上去参加聚会会不会浪费时间？','我总因为怕别人失望而答应邀约。']:
    vector=semantic.encode_query(query)
    result.append({'query':query,'scores':[{'text':text,'score':round(semantic.cosine(vector,stored),3)} for text,stored in zip(texts,vectors)]})
Path('artifacts/library-retrieval-calibration.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'model':semantic.MODEL_NAME,'ready':True,'dimensions':len(vectors[0])}))
