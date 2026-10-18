"""Offline OCR/index jobs; explicit state transitions retain originals on failure."""
import queue
import threading
from django.db import close_old_connections, transaction
from workbench.models import LibraryEntry, Source
from . import library

_ocr = None
_ocr_lock = threading.Lock()


def read_image(path):
    global _ocr
    with _ocr_lock:
        if _ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            _ocr = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
        result, _ = _ocr(path)
        return '\n'.join(row[1] for row in (result or []) if row[2] >= 0.6)


def process_entry(owner, entry_id, *, ocr=None, embed=None):
    item = library._entry(owner,entry_id)
    revision = item.revision
    if item.kind == 'source' and item.parse_state in {'pending','failed'}:
        try:
            source=Source.objects.get(pk=item.object_id)
            pieces=[]
            for asset in item.assets.all():
                if asset.mime.startswith('image/'):
                    text=(ocr or read_image)(asset.original.path)
                    if not text.strip():
                        raise ValueError('no recognized text')
                    pieces.append(text)
                elif asset.mime.startswith('text/'):
                    with asset.original.open('rb') as file:
                        pieces.append(file.read().decode('utf-8-sig'))
            transcript='\n\n'.join(pieces)
            searchable='\n'.join([source.title,source.text,transcript])
            if not searchable.strip() or len(searchable)>50000:
                raise ValueError('empty or too long')
            with transaction.atomic():
                current=library._entry(owner,entry_id)
                if current.revision != revision:
                    return
                Source.objects.filter(pk=source.pk).update(transcript=transcript)
                LibraryEntry.objects.filter(pk=item.pk,revision=revision).update(
                    parse_state='ready',searchable_text=searchable,error_code='')
        except Exception:
            LibraryEntry.objects.filter(pk=item.pk,revision=revision).update(
                parse_state='failed',index_state='blocked',error_code='parse_failed')
            return
    library.index_entry(owner,entry_id,embed=embed)


class IndexWorker:
    def __init__(self):
        self.queue=queue.Queue(maxsize=200)
        self.pending=set()
        self.lock=threading.Lock()
        self.thread=threading.Thread(target=self.run,daemon=True,name='library-index')

    def start(self):
        self.thread.start()
        # Resume work interrupted by a restart. Failed work waits for explicit retry.
        for entry in LibraryEntry.objects.filter(removed=False,index_state='pending')[:200]:
            self.enqueue(entry.owner,entry.pk)

    def enqueue(self,owner,entry_id):
        key=(owner,entry_id)
        with self.lock:
            if key in self.pending:
                return
            try:
                self.queue.put_nowait(key)
                self.pending.add(key)
            except queue.Full:
                return  # Remains visibly pending; retry or restart can resume.

    def run(self):
        while True:
            key=self.queue.get()
            close_old_connections()
            revision=LibraryEntry.objects.filter(owner=key[0],pk=key[1]).values_list('revision',flat=True).first()
            try:
                process_entry(*key)
            except Exception:
                pass  # Removed/revised while queued; no submitted text is logged.
            finally:
                close_old_connections()
                with self.lock:
                    self.pending.discard(key)
                self.queue.task_done()
                if LibraryEntry.objects.filter(owner=key[0],pk=key[1],removed=False,index_state='pending').exclude(revision=revision).exists():
                    self.enqueue(*key)
