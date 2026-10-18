"""Independent synthetic retrieval/OCR acceptance; no account or personal data."""
import json
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from PIL import Image,ImageDraw,ImageFont
from workbench.services import semantic

root=Path(__file__).resolve().parents[1]
rows=[
    '我不必靠频繁参加聚会证明自己合群。独处让我整理思路、恢复精力，重要的是自主选择。',
    '我做重大决定之前会核实关键事实，不因为别人的强烈语气就仓促改变计划。',
    '我上周虽然不想参加同学聚餐，但到场后聊得很开心。小范围、有共同话题的交流也可以补充能量。',
    'Python 列表推导式可以用 [x * 2 for x in items] 对序列中的每个元素执行操作。',
]
cases=[
    ('最近别人都在约饭，我其实想一个人待着，又怕显得不合群。',0,True),
    ('只要不喜欢社交就永远拒绝邀约吗？我好像也有聚会后心情变好的时候。',2,True),
    ('别人说得很肯定的时候，我就容易马上跟着改变主意，怎么缓一缓？',1,True),
    ('Python 怎么把一个列表的元素乘二？',3,True),
    ('帮我写一条 SQL 查询，按照月份汇总订单总额。',None,False),
    ('解释一下月食是怎样形成的。',None,False),
    ('今天我想清净一会儿，但朋友圈都在晒聚餐，我是不是太孤僻了？',0,True),
    ('遇到强势的人，我很快就把自己的判断让出去了。',1,True),
    ('人与人交往一定让我耗电吗？我记得有些饭局之后反而轻松了。',2,True),
    ('CSS 怎样让一个元素水平居中？',None,False),
    ('解释一下二分查找的时间复杂度。',None,False),
    ('教我煮一碗番茄鸡蛋面。',None,False),
]
started=time.perf_counter()
vectors=semantic.encode(rows)
results=[]
for query,expected,should_match in cases:
    t=time.perf_counter()
    vector=semantic.encode_query(query)
    scores=[semantic.cosine(vector,v) for v in vectors]
    best=max(range(len(rows)),key=lambda i:scores[i])
    recalled=[i for i,score in enumerate(scores) if score>=max(.48,max(scores)-.10)]
    passed=(expected in recalled) if should_match else not recalled
    results.append({'query':query,'scores':[round(s,3) for s in scores],'expected':expected,
        'best':best,'recalled':recalled,'passed':passed,'query_ms':round((time.perf_counter()-t)*1000)})
image=Image.new('RGB',(1000,420),'white')
draw=ImageDraw.Draw(image)
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',36)
for i,line in enumerate(['收藏素材示例','不必为了合群而参加每一场聚会。','独处让我恢复精力，也有空间整理思路。']):
    draw.text((40,45+90*i),line,font=font,fill='black')
path=root/'artifacts'/'library-ocr-fixture.png'
image.save(path)
from workbench.services.library_processing import read_image
text=read_image(str(path))
report={'retrieval':results,'ocr_text':text,'ocr_passed':'独处' in text and '合群' in text,
    'total_seconds':round(time.perf_counter()-started,2)}
(root/'artifacts'/'library-semantic-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
sys.exit(0 if all(r['passed'] for r in results) and report['ocr_passed'] else 1)
