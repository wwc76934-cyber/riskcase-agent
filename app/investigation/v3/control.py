"""Request scheduling and immutable reads. Host budgeting cannot add evidence."""
from copy import deepcopy
import threading
from app.investigation.contracts import digest
from app.investigation.v3.output import OutputFailure


def next_stage(*,used_requests,max_requests,extraction_complete=False):
    if type(used_requests) is not int or type(max_requests) is not int or not 2<=max_requests<=6 or not 0<=used_requests<=max_requests:
        raise ValueError('Bounded request counters required')
    remaining=max_requests-used_requests
    if extraction_complete:return 'report' if remaining else 'budget_exhausted'
    if remaining<2:return 'budget_exhausted'
    return 'extract' if remaining==2 else 'investigate'


class ImmutableReads:
    """Only trusted versioned source identities can be cached; not arbitrary tools."""
    def __init__(self):self._values={};self._versions={};self.receipts=[]

    def read(self,identity,version,expected_hash,fetch):
        key=(identity,version,expected_hash)
        version_key=(identity,version)
        if version_key in self._versions and self._versions[version_key]!=expected_hash:
            raise OutputFailure('immutable_version_hash_changed')
        self._versions[version_key]=expected_hash
        if key in self._values:
            self.receipts.append(dict(source_id=identity,version=version,outcome='cache_hit',dispatched=False))
            return deepcopy(self._values[key])
        try:result=fetch()
        except FileNotFoundError:
            self.receipts.append(dict(source_id=identity,version=version,outcome='source_missing',dispatched=True))
            raise OutputFailure('source_missing') from None
        except (TimeoutError,ConnectionError):
            self.receipts.append(dict(source_id=identity,version=version,outcome='tool_unavailable',dispatched=True))
            raise OutputFailure('tool_unavailable') from None
        if digest(result)!=expected_hash:raise OutputFailure('read_hash_changed')
        self._values[key]=deepcopy(result)
        self.receipts.append(dict(source_id=identity,version=version,outcome='acquired',dispatched=True))
        return deepcopy(result)


def journaled_response(store,claim,ordinal,request,invoke,*,audit_snapshot=lambda:None):
    """Reuse existing fenced journal. Uncertain calls are never dispatched twice.

    The caller supplies its authorized budgeted client; this module has no keys,
    network factory, settlement logic, retries or paid execution command.
    """
    replay=store.begin_call(claim['id'],claim['owner'],claim['generation'],ordinal,digest(request))
    if replay is not None:return replay['raw_response'],True
    try:response=invoke()
    except BaseException:
        store.uncertain_call(claim['id'],claim['owner'],claim['generation'],ordinal,audit_snapshot())
        raise
    # An envelope distinguishes a saved null/empty response from an unstarted
    # request. Save before parsing, including a provider's invalid response.
    store.complete_call(claim['id'],claim['owner'],claim['generation'],ordinal,dict(raw_response=response),audit_snapshot())
    return response,False


def with_lease_heartbeat(store,claim,invoke,*,lease_seconds=120,interval=15):
    """Keep the existing lease fenced during synchronous transport, not a retry.

    A heartbeat error cannot cancel an already-sent provider request. Its returned
    response/cost remains in RecordedClient's archive; committing is then blocked.
    """
    if type(lease_seconds) is not int or not 3<=lease_seconds<=600 or not 0<interval<lease_seconds/2:
        raise ValueError('Bounded heartbeat shorter than lease required')
    store.checkpoint(claim['id'],claim['owner'],claim['generation'],lease_seconds=lease_seconds)
    stop=threading.Event();errors=[]
    def renew():
        while not stop.wait(interval):
            try:store.checkpoint(claim['id'],claim['owner'],claim['generation'],lease_seconds=lease_seconds)
            except BaseException as exc:errors.append(exc);return
    thread=threading.Thread(target=renew,name='v3-lease-heartbeat',daemon=True);thread.start()
    try:
        response=invoke()
    finally:
        stop.set();thread.join(timeout=12)
        if thread.is_alive():raise OutputFailure('heartbeat_shutdown_incomplete')
    if errors:raise errors[0]
    # Check the task deadline even when the lease itself is still valid, and
    # after any last heartbeat has finished. A late response stays archived.
    store.checkpoint(claim['id'],claim['owner'],claim['generation'],lease_seconds=lease_seconds)
    return response
