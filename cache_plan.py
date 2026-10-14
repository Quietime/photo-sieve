"""Shared horizons for memory previews, disk prefetch and face detection.

Counts include the current photo. A user setting is a ceiling; disk prefetch
reserves room for thumbnails, face PNGs and undo instead of filling its budget.
"""
PHOTO_BYTES = 512 * 1024
FACE_RAM_MAX = 128

def photo_limits(memory_count,warm_count,disk_bytes,estimated_bytes=PHOTO_BYTES):
    memory=max(1,int(memory_count))
    capacity=max(0,int(max(0,disk_bytes)*.40//max(PHOTO_BYTES,estimated_bytes)))
    disk=min(max(0,int(warm_count)),capacity)
    return memory,disk

def face_limits(requested_ahead,memory_count,warm_count):
    requested=max(0,min(500,int(requested_ahead)));memory=max(1,int(memory_count));disk=max(0,int(warm_count))
    memory_ahead=min(requested,memory-1)
    detection_ahead=min(requested,max(memory,disk)-1)
    return memory_ahead,detection_ahead,min(FACE_RAM_MAX,1+memory_ahead+4)
