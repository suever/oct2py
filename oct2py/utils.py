"""
.. module:: utils
   :synopsis: Miscellaneous helper constructs

.. moduleauthor:: Steven Silvester <steven.silvester@ieee.org>

"""
import inspect
import dis
import sys

from oct2py.compat import PY2


def get_nout():
    """
    Return the number of return values the caller is expecting.

    Adapted from the ompc project.

    Returns
    =======
    out : int
        Number of arguments expected by caller.

    """
    frame = inspect.currentframe()
    # step into the function that called us
    # nout is two frames back
    frame = frame.f_back.f_back
    bytecode = frame.f_code.co_code
    if(sys.version_info >= (3, 6)):
        instruction = bytecode[frame.f_lasti + 2]
    else:
        instruction = bytecode[frame.f_lasti + 3]
    instruction = ord(instruction) if PY2 else instruction
    if instruction == dis.opmap['UNPACK_SEQUENCE']:
        if(sys.version_info >= (3, 6)):
            howmany = bytecode[frame.f_lasti + 3]
        else:
            howmany = bytecode[frame.f_lasti + 4]
        howmany = ord(howmany) if PY2 else howmany
        return howmany
    elif instruction in [dis.opmap['POP_TOP'], dis.opmap['PRINT_EXPR']]:
        return 0
    return 1


class Oct2PyError(Exception):
    """ Called when we can't open Octave or Octave throws an error
    """
    pass


class Struct(dict):
    """
    Octave style struct, enhanced.

    Notes
    =====
    Supports dictionary and attribute style access.  Can be pickled,
    and supports code completion in a REPL.

    Examples
    ========
    >>> from pprint import pprint
    >>> from oct2py import Struct
    >>> a = Struct()
    >>> a.b = 'spam'  # a["b"] == 'spam'
    >>> a.c["d"] = 'eggs'  # a.c.d == 'eggs'
    >>> pprint(a)
    {'b': 'spam', 'c': {'d': 'eggs'}}
    """
    @classmethod
    def from_value(cls, value, session=None):
        """Create a struct from an Octave value and optional session.
        """
        instance = Struct()
        for name in value.dtype.names:
            data = value[name]
            if isinstance(data, np.ndarray) and data.dtype.kind == 'O':
                data = value[name].squeeze().tolist()
            instance[name] = _extract_data(data, session)
        return instance

    def __getattr__(self, attr):
        # Access the dictionary keys for unknown attributes.
        try:
            return self[attr]
        except KeyError:
            msg = "'Struct' object has no attribute %s" % attr
            raise AttributeError(msg)

    def __getitem__(self, attr):
        # Get a dict value; create a Struct if requesting a Struct member.
        # Do not create a key if the attribute starts with an underscore.
        if attr in self.keys() or attr.startswith('_'):
            return dict.__getitem__(self, attr)
        frame = inspect.currentframe()
        # step into the function that called us
        if frame.f_back.f_back and self._is_allowed(frame.f_back.f_back):
            dict.__setitem__(self, attr, Struct())
        elif self._is_allowed(frame.f_back):
            dict.__setitem__(self, attr, Struct())
        return dict.__getitem__(self, attr)

    def _is_allowed(self, frame):
        # Check for allowed op code in the calling frame.
        allowed = [dis.opmap['STORE_ATTR'], dis.opmap['LOAD_CONST'],
                   dis.opmap.get('STOP_CODE', 0)]
        bytecode = frame.f_code.co_code
        instruction = bytecode[frame.f_lasti + 3]
        instruction = ord(instruction) if PY2 else instruction
        return instruction in allowed

    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

    @property
    def __dict__(self):
        # Allow for code completion in a REPL.
        return self.copy()


class StructArray(object):
    """A Python representation of an Octave structure array.

    Notes
    =====
    Supports value access by index and accessing fields by name or value.
    Differs slightly from numpy indexing in that a single number index
    is used to find the nth element in the array, mimicking
    the behavior of a struct array in Octave.

    This class is not meant to be directly created by the user.  It is
    created automatically for structure array values received from Octave.

    Examples
    ========
    >>> from oct2py import octave
    >>> # generate the struct array
    >>> octave.eval('foo = struct("bar", {1, 2}, "baz", {3, 4});')
    >>> foo = octave.pull('foo')
    >>> foo.bar  # value access
    [1.0, 2.0]
    >>> foo['baz']  # item access
    [3.0, 4.0]
    >>> ell = foo[0]  # index access
    >>> foo[1]['baz']
    4.0
    """
    @classmethod
    def from_value(cls, value, session=None):
        """Create a struct array from an Octave value and optional seesion."""
        instance = StructArray()
        for i in range(value.size):
            index = np.unravel_index(i, value.shape)
            for name in value.dtype.names:
                value[index][name] = _extract_data(value[index][name], session)
        instance._value = value
        return instance

    @classmethod
    def to_value(cls, instance):
        return instance._value

    @property
    def fieldnames(self):
        """The field names of the struct array."""
        return self._value.dtype.names

    def __setattr__(self, attr, value):
        if attr not in ['_value', '_session'] and attr in self.fieldnames:
            self._value[attr] = value
        else:
            object.__setattr__(self, attr, value)

    def __getattr__(self, attr):
        # Access the dictionary keys for unknown attributes.
        try:
            return self[attr]
        except KeyError:
            msg = "'StructArray' object has no attribute %s" % attr
            raise AttributeError(msg)

    def __getitem__(self, attr):
        # Get an item from the struct array.
        value = self._value

        # Get the values as a nested list.
        if attr in self.fieldnames:
            return _extract_data(value[attr])

        # Support simple indexing.
        if isinstance(attr, int):
            if attr >= value.size:
                raise IndexError('Index out of range')
            index = np.unravel_index(attr, value.shape)
            return StructElement(self._value[index])

        # Otherwise use numpy indexing.
        data = value[attr]
        # Return a single value as a struct.
        if data.size == 1:
            return StructElement(data)
        instance = StructArray()
        instance._value = data
        return instance

    def __repr__(self):
        shape = self._value.shape
        if len(shape) == 1:
            shape = (shape, 1)
        msg = 'x'.join(str(i) for i in shape)
        msg += ' struct array containing the fields:'
        for key in self.fieldnames:
            msg += '\n    %s' % key
        return msg

    @property
    def __dict__(self):
        # Allow for code completion in a REPL.
        data = dict()
        for key in self.fieldnames:
            data[key] = None
        return data


class StructElement(object):
    """An element of a structure array.

    Notes
    -----
    Supports value access by index and accessing fields by name or value.
    Does not support adding or removing fields.

    This class is not meant to be directly created by the user.  It is
    created automatically for structure array values received from Octave.

    Examples
    --------
    >>> from oct2py import octave
    >>> # generate the struct array
    >>> octave.eval('foo = struct("bar", {1, 2}, "baz", {3, 4});')
    >>> foo = octave.pull('foo')
    >>> el = foo[0]
    >>> el['baz']
    3.0
    >>> el.bar = 'spam'
    >>> el.bar
    'spam'
    """

    def __init__(self, data):
        """Create a new struct element"""
        self._data = data

    @classmethod
    def to_value(cls, instance):
        return instance._data

    @property
    def fieldnames(self):
        """The field names of the struct array element."""
        return self._data.dtype.names

    def __getattr__(self, attr):
        # Access the dictionary keys for unknown attributes.
        if not attr.startswith('_') and attr in self.fieldnames:
            return self.__getitem__(attr)
        return object.__getattr__(self, attr)

    def __setattr__(self, attr, value):
        if not attr.startswith('_') and attr in self.fieldnames:
            self._data[attr] = value
            return
        object.__setattr__(self, attr, value)

    def __getitem__(self, item):
        if item in self.fieldnames:
            return self._data[item]
        raise IndexError('Invalid index')

    def __setitem__(self, item, value):
        self._data[item] = value

    def __repr__(self):
        msg = 'struct array element containing the fields:'
        for key in self.fieldnames:
            msg += '\n    %s' % key
        return msg

    def __dict__(self):
        """Allow for code completion in a REPL"""
        data = dict()
        for key in self.fieldnames:
            data[key] = None
        return data


def read_file(path, session=None):
    """Read the data from the given file path.
    """
    try:
        data = loadmat(path, struct_as_record=True)
    except UnicodeDecodeError as e:
        raise Oct2PyError(str(e))
    out = dict()
    for (key, value) in data.items():
        out[key] = _extract_data(value, session)
    return out


def _extract_data(data, session=None):
    # Extract each item of a list.
    if isinstance(data, list):
        return [_extract_data(v, session) for v in data]

    # Ignore leaf objects.
    if not isinstance(data, np.ndarray):
        return data

    # Convert user defined classes.
    if hasattr(data, 'classname') and session:
        cls = session._get_user_class(data.classname)
        data = cls.from_value(data)

    # Extract struct data.
    if data.dtype.names:
        # Singular struct
        if data.size == 1:
            data = Struct.from_value(data, session)
        # Struct array
        else:
            data = StructArray.from_value(data, session)

    # Extract cells.
    elif data.dtype.kind == 'O':
        data = data.squeeze().tolist()
        if not isinstance(data, list):
            data = [data]
        data = _extract_data(data, session)

    # Compress singleton values.
    elif data.size == 1:
        data = data.item()

    # Compress empty values.
    elif data.size == 0:
        if data.dtype.kind in 'US':
            data = ''
        else:
            data = []

    # Return parsed value.
    return data


def get_log(name=None):
    """Return a console logger.

    Output may be sent to the logger using the `debug`, `info`, `warning`,
    `error` and `critical` methods.

    Parameters
    ----------
    name : str
        Name of the log.

    References
    ----------
    .. [1] Logging facility for Python,
           http://docs.python.org/library/logging.html

    """
    import logging

    if name is None:
        name = 'oct2py'
    else:
        name = 'oct2py.' + name

    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    return log


def _setup_log():
    """Configure root logger.

    """
    import logging
    import sys

    try:
        handler = logging.StreamHandler(stream=sys.stdout)
    except TypeError:  # pragma: no cover
        handler = logging.StreamHandler(strm=sys.stdout)

    log = get_log()
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


_setup_log()

