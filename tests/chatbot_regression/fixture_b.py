from backend.app.runtime import EventPipeline
import runpy

original = EventPipeline.spaces
def spaces(self, lot_id, *, as_of):
    rows = original(self, lot_id, as_of=as_of)
    if lot_id == 'A':
        for row in rows:
            if row['space_id'] == 'A-06':
                row['status'] = 'unavailable'
    return rows
EventPipeline.spaces = spaces
runpy.run_module('backend.app.main', run_name='__main__')
