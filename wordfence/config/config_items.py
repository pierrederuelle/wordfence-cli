import base64
import inspect
import json
from dataclasses import dataclass, fields
from enum import Enum
from os import path
from typing import Optional, Any, Dict, List, Iterable, Set, FrozenSet
from functools import lru_cache
import abc

CONFIG_DEFINITIONS_FILENAME = 'config_definitions.json'
CONFIG_DEFINITIONS_PATH: str = path.dirname(
    inspect.getfile(inspect.currentframe())) + path.sep + CONFIG_DEFINITIONS_FILENAME

KIBIBYTE = 1024
MEBIBYTE = 1024 * 1024

sizings_map = {
    'b': 1,
    'k': KIBIBYTE,
    'kb': KIBIBYTE,
    'kib': KIBIBYTE,
    'm': MEBIBYTE,
    'mb': MEBIBYTE,
    'mib': MEBIBYTE
}
"""maps suffixes to byte multipliers; k/kb/kib are synonyms, as are m/mb/mib"""


class Context(Enum):
    ALL = 1
    """a config item that is available in both the CLI and INI contexts"""
    CLI = 2
    """a config item that is only available in the CLI context (not INI)"""
    CONFIG = 3
    """a config item that is only available in the INI context (not CLI)"""


class ArgumentType(Enum):
    ARGUMENT = 1
    """No names, just ordered CLI values"""
    FLAG = 2
    """boolean values set by name (no value) -- inverts the default value when provided"""
    # FLAG: argument name + value
    OPTION = 3
    """required the option name plus a value"""
    OPTION_REPEATABLE = 4
    """an option that can be repeated multiple times with different values"""


@dataclass(frozen=True)
class ReferenceToken:
    """Instantiate a new instance to use the `x is y` language construct to determine if other instances point to the
    same token"""
    # TODO add required label; keep track of instantiations and throw error if same label is instantiated twice in __new__ or __init__
    pass


not_set_token = ReferenceToken()


@dataclass(frozen=True)
class ConfigItemMeta:
    valid_options: Optional[FrozenSet[str]] = None
    multiple: Optional[bool] = None
    ini_separator: Optional[str] = None
    value_type: str = 'str'


valid_types = {
    'str': str,
    'string': str,
    'int': int,
    'bool': bool
}


@dataclass(frozen=True)
class ConfigItemDefinition:
    name: str
    property_name: str
    description: str
    context: Context
    argument_type: ArgumentType
    default: Any
    short_name: Optional[str] = None
    meta: Optional[ConfigItemMeta] = None

    @staticmethod
    def clean_argument_dict(source: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in source.items() if key in get_data_item_fields()}

    def has_options_list(self) -> bool:
        return True if self.meta and self.meta.valid_options else False

    def has_ini_separator(self) -> bool:
        return True if self.meta and self.meta.ini_separator else False

    def get_value_type(self):
        if not self.meta:
            return str if self.argument_type != ArgumentType.FLAG else bool
        return_type = valid_types.get(self.meta.value_type, False)
        if not return_type:
            raise ValueError(f"Specified type not in the allow list: {self.meta.value_type}")
        return return_type

    @classmethod
    def from_dict(cls, source: dict):
        # The property name is always derived from the configuration's "name" value. Any "property_name" value specified
        # in the configuration is ignored.
        source['property_name'] = source['name'].replace('-', '_')

        if source.get('default_type', None) == 'base64':
            if 'default' not in source:
                raise ValueError("When base64 default type is specified, a default value must be present")
            source['default'] = base64.b64decode(source['default'])

        if 'default' not in source:
            source['default'] = not_set_token
        # convert enums
        source['context'] = source['context'] if isinstance(source['context'], Context) else Context[source['context']]
        source['argument_type'] = source['argument_type'] if isinstance(source['argument_type'], ArgumentType) else \
            ArgumentType[
                source['argument_type']]

        # convert the meta dict to a meta object to make it hashable
        if source.get('meta', False):
            # convert lists to tuples to make them hashable
            if source['meta'].get('valid_options', False):
                source['meta']['valid_options'] = tuple(source['meta']['valid_options'])
            # set flags to booleans types if another type is not already defined
            if not_set_token is source['meta'].get('value_type', not_set_token) and source['argument_type'] \
                    == ArgumentType.FLAG:
                source['meta']['value_type'] = 'bool'
            source['meta'] = ConfigItemMeta(**source['meta'])

        # sanity check
        if ArgumentType.FLAG == source['argument_type'] and not isinstance(source['default'], bool):
            raise ValueError(f"Flag {source['name']} has a non-boolean value type defined: {type(source['default'])}")
        return cls(**ConfigItemDefinition.clean_argument_dict(source))

    @classmethod
    def from_json(cls, source: str):
        return cls.from_dict(json.loads(source))


@dataclass(frozen=True)
class ConfigValue:
    definition: ConfigItemDefinition
    value: Any


class CanonicalValueExtractorInterface(metaclass=abc.ABCMeta):
    @classmethod
    def __subclasshook__(cls, subclass):
        return (callable(subclass.get_canonical_value),
                callable(subclass.is_valid_source),
                callable(subclass.assert_is_valid_source))

    @classmethod
    @abc.abstractmethod
    def is_valid_source(cls, source: Any) -> bool:
        """Validate the source is supported"""
        raise NotImplementedError

    @classmethod
    def assert_is_valid_source(cls, source: Any) -> None:
        if not cls.is_valid_source(source):
            raise ValueError(f"Invalid configuration source: {type(source)}")

    @classmethod
    @abc.abstractmethod
    def get_canonical_value(cls, definition: ConfigItemDefinition, source: Any) -> Any:
        """Return the canonical configuration value as stored in the configuration source"""
        raise NotImplementedError


class AlwaysInvalidExtractor(CanonicalValueExtractorInterface):
    """Always throws an exception when a value is extracted"""

    @classmethod
    def is_valid_source(cls, source: Any) -> bool:
        return False

    @classmethod
    def get_canonical_value(cls, definition: ConfigItemDefinition, source: Any) -> Any:
        cls.assert_is_valid_source(source)


@lru_cache(maxsize=1)
def get_data_item_fields() -> Set[str]:
    return set([x.name for x in fields(ConfigItemDefinition)])


def get_config_definitions_map() -> Dict[str, ConfigItemDefinition]:
    result: Dict[str, ConfigItemDefinition] = {}
    used_short_names: Set[str] = set()
    with open(CONFIG_DEFINITIONS_PATH) as json_file:
        for value in json.load(json_file):
            config_item = ConfigItemDefinition.from_dict(value)
            if config_item.name in result:
                raise KeyError(f"The name {config_item.name} has already been loaded")
            if config_item.short_name:
                if config_item.short_name in used_short_names:
                    raise KeyError(f"The short name {config_item.short_name} has already been loaded")
                else:
                    used_short_names.add(config_item.short_name)
            result[config_item.name] = config_item
    return result


definitions = get_config_definitions_map()
