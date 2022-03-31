"""
`Reference <https://github.com/certified-spec/specPy/blob/master/doc/specformat.rst>`_
for the spec file format.
"""
import event_model
from datetime import datetime
import os
from pathlib import Path
import jinja2
import suitcase.utils
from ._version import get_versions

__version__ = get_versions()['version']
del get_versions


# TODO Just use f-string?
env = jinja2.Environment()


SPEC_TIME_FORMAT = '%a %b %d %H:%M:%S %Y'


def from_spec_time(string_time):
    """Convert the spec time in line #D to a Python datetime object

    Parameters
    ----------
    string_time : str
        The SPEC string representation of time. e.g.: Fri Feb 19 14:01:35 2016

    Returns
    -------
    datetime.datetime object
    """
    return datetime.strptime(string_time, SPEC_TIME_FORMAT)


def to_spec_time(datetime_object):
    """Convert a datetime object into the SPEC line #D

    Parameters
    ----------
    datetime_object : datetime.datetime object

    Returns
    -------
    str
        The string representation of SPEC time: e.g., Fri Feb 19 14:01:35 2016
    """
    return datetime_object.strftime(SPEC_TIME_FORMAT)


_SPEC_FILE_HEADER_TEMPLATE = env.from_string("""#F {{ filename }}
#E {{ unix_time }}
#D {{ readable_time }}
#C {{ owner }}  User = {{ owner }}
#O0 {{ positioner_variable_sources | join ('  ') }}
#o0 {{ positioner_variable_names | join(' ') }}""")


_DEFAULT_POSITIONERS = {'data_keys': {}}


def to_spec_file_header(start, filepath, baseline_descriptor=None):
    """Generate a spec file header from some documents

    Parameters
    ----------
    start : Document or dict
        The RunStart that is emitted by the bluesky.run_engine.RunEngine or
        something that is compatible with that format
    filepath : str
        The filename of this spec scan. Will use os.path.basename to find the
        filename
    baseline_descriptor : Document or dict, optional
        The 'baseline' Descriptor document that is emitted by the RunEngine
        or something that is compatible with that format.
        Defaults to the values in suitcase.spec._DEFAULT_POSITIONERS

    Returns
    -------
    str
        The formatted SPEC file header. You probably want to split on "\n"
    """
    if baseline_descriptor is None:
        baseline_descriptor = _DEFAULT_POSITIONERS
    md = {}
    md['owner'] = start.get('owner', '')
    md['positioner_variable_names'] = sorted(
            list(baseline_descriptor['data_keys'].keys()))
    md['positioner_variable_sources'] = [
        baseline_descriptor['data_keys'][k]['source'] for k
        in md['positioner_variable_names']]
    md['unix_time'] = int(start['time'])
    md['readable_time'] = to_spec_time(datetime.fromtimestamp(md['unix_time']))
    md['filename'] = os.path.basename(filepath)
    return _SPEC_FILE_HEADER_TEMPLATE.render(md)


_SPEC_1D_COMMAND_TEMPLATE = env.from_string(
    "{{ plan_name }} {{ scan_motor }} {{ start }} {{ stop }} {{ num }} {{ time }}")

_SCANS_WITHOUT_MOTORS = {'ct': 'count'}
_SCANS_WITH_MOTORS = {'ascan': 'scan', 'dscan': 'rel_scan'}
_SPEC_SCAN_NAMES = _SCANS_WITHOUT_MOTORS.copy()
_SPEC_SCAN_NAMES.update(_SCANS_WITH_MOTORS)
_BLUESKY_PLAN_NAMES = {v: k for k, v in _SPEC_SCAN_NAMES.items()}


def get_name(plan_name):
    return _BLUESKY_PLAN_NAMES.get(plan_name, 'Other')


_SPEC_SCAN_HEADER_TEMPLATE = env.from_string("""

#S {{ scan_id }} {{ command }}
#D {{ readable_time }}
#T {{ acq_time }}  (Seconds)
#P0 {{ positioner_positions | join(' ')}}
#N {{ num_columns }}
#L {{ motor_name }}  Epoch  Seconds  {{ data_keys | join('  ') }}
""")


def _get_acq_time(start, default_value=-1):
    """Private helper function to extract the heuristic count time

    The SPEC-style plans inject a heuristic 'count_time' (which has
    different meanings in different contexts) as a top-level key in the
    RunStart document.

    Parameters
    ----------
    start : Document or dict
        The RunStart document emitted by the bluesky RunEngine or a dictionary
        that has compatible information
    default_value : int, optional
        The default acquisition time. Defaults to -1
    """
    time = start.get('count_time', default_value)
    if time is None:
        # 'None' is not legal in spec
        time = default_value
    return time


def _get_plan_name(start):
    plan_name = start['plan_name']
    return get_name(plan_name)


def _get_motor_name(start):
    plan_name = _get_plan_name(start)
    if (plan_name not in _SPEC_SCAN_NAMES or
            plan_name in _SCANS_WITHOUT_MOTORS):
        return 'seq_num'
    motor_name = start['motors']
    # We only support a single scanning motor right now.
    if len(motor_name) > 1:
        raise NotImplementedError(
            "Your scan has {0} scanning motors. They are {1}. Conversion to a"
            "specfile does not understand what to do with multiple scanning. "
            "Please request this feature at "
            "https://github.com/NSLS-II/suitcase/issues Until this feature is "
            "implemented, we will be using the sequence number as the motor "
            "position".format(len(motor_name), motor_name))
        return 'seq_num'
    return motor_name[0]


def _get_motor_position(start, event):
    plan_name = _get_plan_name(start)
    # make sure we are trying to get the motor position for an implemented scan
    if (plan_name not in _SPEC_SCAN_NAMES or
            plan_name in _SCANS_WITHOUT_MOTORS):
        return event['seq_num']
    motor_name = _get_motor_name(start)
    # make sure we have a motor name that we can get data for. Otherwise we use
    # the sequence number of the event
    if motor_name == 'seq_num':
        return event['seq_num']
    # if none of the above conditions are met, we can get a motor value. Thus we
    # return the motor value in the event
    return event['data'][motor_name]


def _get_scan_data_column_names(start, primary_descriptor):
    motor_name = _get_motor_name(start)
    # List all scalar fields, excluding the motor (x variable).
    read_fields = sorted(
        [k for k, v in primary_descriptor['data_keys'].items()
         if (v['object_name'] != motor_name and not v['shape'])])
    return read_fields


def to_spec_scan_header(start, primary_descriptor, baseline_event=None):
    """Convert the RunStart, "primary" Descriptor and the "baseline" Event
    into a spec scan header

    Parameters
    ----------
    start : Document or dict
        The RunStart document emitted by the bluesky RunEngine or a dictionary
        that has compatible information
    primary_descriptor : Document or dict
        The Descriptor that corresponds to the main event stream
    baseline_event : Document or dict, optional
        The Event that corresponds to the mass reading of motors before the
        scan begins.
        Default value is `-1` for each of the keys in
        `suitcase.spec._DEFAULT_POSITIONERS`

    Returns
    -------
    str
        The formatted SPEC scan header. You probably want to split on "\n"
    """
    if baseline_event is None:
        baseline_event = {
            'data':
                {k: -1 for k in _DEFAULT_POSITIONERS['data_keys']}}
    md = {}
    md['scan_id'] = start['scan_id']
    scan_command = _get_plan_name(start)
    motor_name = _get_motor_name(start)
    acq_time = _get_acq_time(start)
    # can only grab start/stop/num if we are a dscan or ascan.
    if (scan_command not in _SPEC_SCAN_NAMES or
            scan_command in _SCANS_WITHOUT_MOTORS):
        command_args = []

    else:

        start_val = start['plan_args']['args'][-2]
        stop_val = start['plan_args']['args'][-1]
        num = start['plan_args']['num']

        command_args = [start_val, stop_val, num]

    command_list = ([scan_command, motor_name] + command_args + [acq_time])
    # have to ensure all list elements are strings or join gets angry
    md['command'] = ' '.join([str(s) for s in command_list])
    md['readable_time'] = to_spec_time(datetime.fromtimestamp(start['time']))
    md['acq_time'] = acq_time
    md['positioner_positions'] = [
        v for k, v in sorted(baseline_event['data'].items())]
    md['data_keys'] = _get_scan_data_column_names(start, primary_descriptor)
    md['num_columns'] = 3 + len(md['data_keys'])
    md['motor_name'] = _get_motor_name(start)
    return _SPEC_SCAN_HEADER_TEMPLATE.render(md)


_SPEC_EVENT_TEMPLATE = env.from_string("""
{{ motor_position }}  {{ unix_time }} {{ acq_time }} {{ values | join(' ') }}""")


def to_spec_scan_data(start, primary_descriptor, event):
    md = {}
    md['unix_time'] = int(event['time'])
    md['acq_time'] = _get_acq_time(start)
    md['motor_position'] = _get_motor_position(start, event)
    data_keys = _get_scan_data_column_names(start, primary_descriptor)
    md['values'] = [event['data'][k] for k in data_keys]
    return _SPEC_EVENT_TEMPLATE.render(md)


# Dictionary that maps a spec metadata line to a specific lambda function
# to parse it. This only works for lines whose contents can be mapped to a
# single semantic meaning.  e.g., the "spec command" line
# (ascan start stop step exposure_time) does not map well on to this "single
# semantic meaning" splitter
spec_line_parser = {
    '#D': ('time_from_date', from_spec_time),
    '#E': ('time',
           lambda x: datetime.fromtimestamp(int(x))),
    '#F': ('filename', str),
    # The exposure time
    '#N': ('num_intervals', int),
    # The h, k, l coordinates
    '#Q': ('hkl', lambda x: [float(s) for s in x.split(' ')]),
    '#T': ('exposure_time', lambda x: float(x.split('  ')[0])),
}


def export(gen, directory, file_prefix='{start[uid]}'):
    """
    Export a stream of documents to a specfile.

    .. note::

        This can alternatively be used to write data to generic buffers rather
        than creating files on disk. See the documentation for the
        ``directory`` parameter below.

    Parameters
    ----------
    gen : generator
        expected to yield ``(name, document)`` pairs

    directory : string, Path or Manager.
        For basic uses, this should be the path to the output directory given
        as a string or Path object. Use an empty string ``''`` to place files
        in the current working directory.

        In advanced applications, this may direct the serialized output to a
        memory buffer, network socket, or other writable buffer. It should be
        an instance of ``suitcase.utils.MemoryBufferManager`` and
        ``suitcase.utils.MultiFileManager`` or any object implementing that
        inferface. See the suitcase documentation at
        https://nsls-ii.github.io/suitcase for details.

    file_prefix : str, optional
        The first part of the filename of the generated output files. This
        string may include templates as in
        ``{start[proposal_id]}-{start[sample_name]}``,
        which are populated from the RunStart document. The default value is
        ``{start[uid]}`` which is guaranteed to be present and unique. A more
        descriptive value depends on the application and is therefore left to
        the user.

    Returns
    -------
    artifacts : dict
        dict mapping the 'labels' to lists of file names (or, in general,
        whatever resources are produced by the Manager)

    Examples
    --------

    Generate files with unique-identifier names in the current directory.

    >>> export(gen, '')

    Generate files with more readable metadata in the file names.

    >>> export(gen, '', '{start[plan_name]}-{start[motors]}-')

    Include the experiment's start time formatted as YYYY-MM-DD_HH-MM.

    >>> export(gen, '', '{start[time]:%Y-%m-%d_%H:%M}-')

    Place the files in a different directory, such as on a mounted USB stick.

    >>> export(gen, '/path/to/my_usb_stick')
    """
    with Serializer(directory, file_prefix) as serializer:
        for item in gen:
            serializer(*item)

    return serializer.artifacts


class Serializer(event_model.DocumentRouter):
    """
    Serialize a stream of documents to specfile.

    .. note::

        This can alternatively be used to write data to generic buffers rather
        than creating files on disk. See the documentation for the
        ``directory`` parameter below.

    Parameters
    ----------
    directory : string, Path, or Manager
        For basic uses, this should be the path to the output directory given
        as a string or Path object. Use an empty string ``''`` to place files
        in the current working directory.

        In advanced applications, this may direct the serialized output to a
        memory buffer, network socket, or other writable buffer. It should be
        an instance of ``suitcase.utils.MemoryBufferManager`` and
        ``suitcase.utils.MultiFileManager`` or any object implementing that
        interface. See the suitcase documentation at
        https://nsls-ii.github.io/suitcase for details.

    file_prefix : str, optional
        The first part of the filename of the generated output files. This
        string may include templates as in
        ``{start[proposal_id]}-{start[sample_name]}``,
        which are populated from the RunStart document. The default value is
        ``{start[uid]}`` which is guaranteed to be present and unique. A more
        descriptive value depends on the application and is therefore left to
        the user.

    flush : boolean
        Flush the file to disk after each document. As a consequence, writing
        the full document stream is slower but each document is immediately
        available for reading. False by default.

    Attributes
    ----------
    artifacts
        dict mapping the 'labels' to lists of file names (or, in general,
        whatever resources are produced by the Manager)

    Notes
    -----
    1. `Reference <https://github.com/certified-spec/specPy/blob/master/doc/specformat.rst>`_
        for the spec file format.
    2. If there is more than one primary descriptor, the behavior of this spec
       callback is undefined.  Please do not use this callback with more than
       one descriptor.
    """
    def __init__(self, directory, file_prefix='{start[uid]}', flush=False):

        self._file_prefix = file_prefix
        self._flush = flush
        self._templated_file_prefix = ''  # set when we get a 'start' document

        if isinstance(directory, (str, Path)):
            # The user has given us a filepath; they want files.
            # Set up a MultiFileManager for them.
            self._manager = suitcase.utils.MultiFileManager(
                directory,
                allowed_modes=('a'))
        else:
            # The user has given us their own Manager instance. Use that.
            self._manager = directory

        self._file = None  # set in start() below
        self.pos_names = ["No", "Positioners", "Were", "Given"]
        self.positions = ["-inf", "-inf", "-inf", "-inf"]
        self._start = None
        self._baseline_descriptor = None
        self._baseline_event = None
        self._primary_descriptor = None
        self._has_not_written_scan_header = True
        self._has_not_written_file_header = True
        self._num_events_received = 0
        self._num_baseline_events_received = 0

    @property
    def artifacts(self):
        # The 'artifacts' are the manager's way to exposing to the user a
        # way to get at the resources that were created. For
        # `MultiFileManager`, the artifacts are filenames.  For
        # `MemoryBuffersManager`, the artifacts are the buffer objects
        # themselves. The Serializer, in turn, exposes that to the user here.
        #
        # This must be a property, not a plain attribute, because the
        # manager's `artifacts` attribute is also a property, and we must
        # access it anew each time to be sure to get the latest contents.
        return self._manager.artifacts

    def close(self):
        """
        Close all of the resources (e.g. files) allocated.
        """
        self._manager.close()

    def __enter__(self):
        return self

    def __exit__(self, *exception_details):
        self.close()

    def start(self, doc):
        """
        Stash the start document and reset the internal state
        """
        self._start = doc

        try:
            self._file = self._manager.open(
                'stream_data',
                f'{self._file_prefix.format(start=doc)}.spec', 'a')
        except suitcase.utils.ModeError as error:
            raise ValueError(
                "To write data from multiple runs into the same specfile, "
                "the Serializer requires a manager that supports append ('a') "
                "mode.") from error
        # Use tell() to sort out if this file is empty (i.e. a new file) and
        # therefore whether we need to write the specfile header or not.
        self._has_not_written_file_header = not self._file.tell()

    def _write_new_header(self):
        filepath, = self._manager.artifacts['stream_data']
        header = to_spec_file_header(self._start, filepath,
                                     self._baseline_descriptor)
        self._file.write(header)

    def descriptor(self, doc):
        if doc.get('name') == 'baseline':
            # if this is the baseline descriptor, we might need to write a
            # new file header
            self._baseline_descriptor = doc
        elif self._primary_descriptor:
            # we already have a primary descriptor, why are we getting
            # another one?
            err_msg = (
                "The suitcase.specfile.Serializer is not designed to handle more "
                "than one descriptor.  If you need this functionality, please "
                "request it at https://github.com/NSLS-II/suitcase/issues. "
                "Until that time, this DocumentToSpec callback will raise a "
                "NotImplementedError if you try to use it with two event "
                "streams.")
            raise NotImplementedError(err_msg)
        else:
            self._primary_descriptor = doc

    def event(self, doc):
        if (self._baseline_descriptor and
                doc['descriptor'] == self._baseline_descriptor['uid']):
            self._num_baseline_events_received += 1
            self._baseline_event = doc
            return
        # Write the scan header as soon as we get the first event.  If it is
        # not the baseline event, then sorry! You need to give me that before
        # any primary events.
        if self._has_not_written_file_header:
            # maybe write a new file header if there is not one already
            self._write_new_header()
            self._has_not_written_file_header = False
        if self._has_not_written_scan_header:
            # write the scan header with whatever information we currently have
            scan_header = to_spec_scan_header(self._start,
                                              self._primary_descriptor,
                                              self._baseline_event)
            self._file.write(scan_header)
            self._has_not_written_scan_header = False

        if doc['descriptor'] != self._primary_descriptor['uid']:
            err_msg = (
                "The DocumentToSpec callback is not designed to handle more "
                "than one event stream.  If you need this functionality, please "
                "request it at https://github.com/NSLS-II/suitcase/issues. "
                "Until that time, this DocumentToSpec callback will raise a "
                "NotImplementedError if you try to use it with two event "
                "streams.")
            raise NotImplementedError(err_msg)
        self._num_events_received += 1
        # now write the scan data line
        scan_data_line = to_spec_scan_data(self._start,
                                           self._primary_descriptor, doc)
        self._file.write(scan_data_line + '\n')
        if self._flush:
            self._file.flush()

    def stop(self, doc):
        msg = '\n'
        doc = doc.copy()
        doc.setdefault('reason', 'No reason recorded.')
        if doc['exit_status'] != 'success':
            msg += ('#C Run exited with status: {exit_status}. Reason: '
                    '{reason}'.format(**doc))
        self._file.write(msg)
        if self._flush:
            self._file.flush()
