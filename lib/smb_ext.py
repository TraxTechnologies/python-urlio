"""
Override pysmb's listPath method to be able to limit the number of
results returned.
"""
from smb.base import _PendingRequest, SharedFile
from smb.smb_constants import *
from smb.smb_structs import *
from smb.smb2_structs import *
DFLTSEARCH = (
    SMB_FILE_ATTRIBUTE_READONLY |
    SMB_FILE_ATTRIBUTE_HIDDEN |
    SMB_FILE_ATTRIBUTE_SYSTEM |
    SMB_FILE_ATTRIBUTE_DIRECTORY |
    SMB_FILE_ATTRIBUTE_ARCHIVE
)

def listPath(conn, service_name, path,
             search = DFLTSEARCH,
             pattern = '*', timeout = 30, limit=0):
    """
    Retrieve a directory listing of files/folders at *path*

    :param string/unicode service_name: the name of the shared folder for the *path*
    :param string/unicode path: path relative to the *service_name* where we are interested to learn about its files/sub-folders.
    :param integer search: integer value made up from a bitwise-OR of *SMB_FILE_ATTRIBUTE_xxx* bits (see smb_constants.py).
                           The default *search* value will query for all read-only, hidden, system, archive files and directories.
    :param string/unicode pattern: the filter to apply to the results before returning to the client.
    :return: A list of :doc:`smb.base.SharedFile<smb_SharedFile>` instances.
    """
    if not conn.sock:
        raise NotConnectedError('Not connected to server')

    results = [ ]

    def cb(entries):
        conn.is_busy = False
        results.extend(entries)

    def eb(failure):
        conn.is_busy = False
        raise failure

    conn.is_busy = True
    try:
        _listPath_SMB2(conn, service_name, path, cb, eb, search = search, pattern =
        pattern, timeout = timeout, limit=limit)
        while conn.is_busy:
            conn._pollForNetBIOSPacket(timeout)
    finally:
        conn.is_busy = False

    return results

def _listPath_SMB2(
        conn, service_name, path, callback, errback, search, pattern,
        timeout=30, limit=0,
    ):
    if not conn.has_authenticated:
        raise NotReadyError('SMB connection not authenticated')

    expiry_time = time.time() + timeout
    path = path.replace('/', '\\')
    if path.startswith('\\'):
        path = path[1:]
    if path.endswith('\\'):
        path = path[:-1]
    messages_history = [ ]
    results = [ ]

    def sendCreate(tid):
        create_context_data = binascii.unhexlify(
            "28 00 00 00 10 00 04 00 00 00 18 00 10 00 00 00 "
            "44 48 6e 51 00 00 00 00 00 00 00 00 00 00 00 00 "
            "00 00 00 00 00 00 00 00 18 00 00 00 10 00 04 00 "
            "00 00 18 00 00 00 00 00 4d 78 41 63 00 00 00 00 "
            "00 00 00 00 10 00 04 00 00 00 18 00 00 00 00 00 "
            "51 46 69 64 00 00 00 00".replace(' ', '').replace('\n', ''))
        m = SMB2Message(SMB2CreateRequest(path,
                                          file_attributes = 0,
                                          access_mask = FILE_READ_DATA | FILE_READ_EA | FILE_READ_ATTRIBUTES | SYNCHRONIZE,
                                          share_access = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                          oplock = SMB2_OPLOCK_LEVEL_NONE,
                                          impersonation = SEC_IMPERSONATE,
                                          create_options = FILE_DIRECTORY_FILE,
                                          create_disp = FILE_OPEN,
                                          create_context_data = create_context_data))
        m.tid = tid
        conn._sendSMBMessage(m)
        conn.pending_requests[m.mid] = _PendingRequest(m.mid, expiry_time, createCB, errback)
        messages_history.append(m)

    def createCB(create_message, **kwargs):
        messages_history.append(create_message)
        if create_message.status == 0:
            sendQuery(create_message.tid, create_message.payload.fid, '')
        else:
            errback(OperationFailure('Failed to list %s on %s: Unable to open directory' % ( path, service_name ), messages_history))

    def sendQuery(tid, fid, data_buf):
        m = SMB2Message(SMB2QueryDirectoryRequest(fid, pattern,
                                                  info_class = 0x03,   # FileBothDirectoryInformation
                                                  flags = 0,
                                                  output_buf_len = conn.max_transact_size))
        m.tid = tid
        conn._sendSMBMessage(m)
        conn.pending_requests[m.mid] = _PendingRequest(m.mid, expiry_time, queryCB, errback, fid = fid, data_buf = data_buf)
        messages_history.append(m)

    def queryCB(query_message, **kwargs):
        messages_history.append(query_message)
        if query_message.status == 0:
            data_buf = decodeQueryStruct(
                kwargs['data_buf'] + query_message.payload.data,
                query_message.tid, kwargs['fid']
            )
            if data_buf is False:
                closeFid(query_message.tid, kwargs['fid'], results = results)
            else:
                sendQuery(query_message.tid, kwargs['fid'], data_buf)
        elif query_message.status == 0x80000006L:  # STATUS_NO_MORE_FILES
            closeFid(query_message.tid, kwargs['fid'], results = results)
        else:
            closeFid(query_message.tid, kwargs['fid'], error = query_message.status)

    def decodeQueryStruct(data_bytes, tid, fid):
        # SMB_FIND_FILE_BOTH_DIRECTORY_INFO structure. See [MS-CIFS]: 2.2.8.1.7 and [MS-SMB]: 2.2.8.1.1
        info_format = '<IIQQQQQQIIIBB24s'
        info_size = struct.calcsize(info_format)

        data_length = len(data_bytes)
        offset = 0
        while offset < data_length:
            if offset + info_size > data_length:
                return data_bytes[offset:]

            next_offset, _, \
            create_time, last_access_time, last_write_time, last_attr_change_time, \
            file_size, alloc_size, file_attributes, filename_length, ea_size, \
            short_name_length, _, short_name = struct.unpack(info_format, data_bytes[offset:offset+info_size])

            offset2 = offset + info_size
            if offset2 + filename_length > data_length:
                return data_bytes[offset:]

            filename = data_bytes[offset2:offset2+filename_length].decode('UTF-16LE')
            short_name = short_name.decode('UTF-16LE')
            results.append(SharedFile(convertFILETIMEtoEpoch(create_time), convertFILETIMEtoEpoch(last_access_time),
                                      convertFILETIMEtoEpoch(last_write_time), convertFILETIMEtoEpoch(last_attr_change_time),
                                      file_size, alloc_size, file_attributes, short_name, filename))

            if limit != 0 and len(results) >= limit:
                return False
            if next_offset:
                offset += next_offset
            else:
                break
        return ''

    def closeFid(tid, fid, results = None, error = None):
        m = SMB2Message(SMB2CloseRequest(fid))
        m.tid = tid
        conn._sendSMBMessage(m)
        conn.pending_requests[m.mid] = _PendingRequest(m.mid, expiry_time, closeCB, errback, results = results, error = error)
        messages_history.append(m)

    def closeCB(close_message, **kwargs):
        if kwargs['results'] is not None:
            callback(kwargs['results'])
        elif kwargs['error'] is not None:
            errback(OperationFailure('Failed to list %s on %s: Query failed with errorcode 0x%08x' % ( path, service_name, kwargs['error'] ), messages_history))

    if not conn.connected_trees.has_key(service_name):
        def connectCB(connect_message, **kwargs):
            messages_history.append(connect_message)
            if connect_message.status == 0:
                conn.connected_trees[service_name] = connect_message.tid
                sendCreate(connect_message.tid)
            else:
                errback(OperationFailure('Failed to list %s on %s: Unable to connect to shared device' % ( path, service_name ), messages_history))

        m = SMB2Message(SMB2TreeConnectRequest(r'\\%s\%s' % ( conn.remote_name.upper(), service_name )))
        conn._sendSMBMessage(m)
        conn.pending_requests[m.mid] = _PendingRequest(m.mid, expiry_time, connectCB, errback, path = service_name)
        messages_history.append(m)
    else:
        sendCreate(conn.connected_trees[service_name])
