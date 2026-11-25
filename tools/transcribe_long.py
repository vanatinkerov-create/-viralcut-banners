import json, sys, time
sys.path.insert(0, '.')
from viralcut import transcribe
t = time.time()
res = transcribe.transcribe(__import__('pathlib').Path('data/test/source_long.mp4'), __import__('pathlib').Path('data/test'), language='ru')
json.dump(res, open('data/test/transcript_long.json', 'w'), ensure_ascii=False)
print('done', time.time()-t, 'segs', len(res['segments']))
