"""ConfigFile helper class."""

from __future__ import annotations

import json
import os
from abc import ABC
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Tuple, Type, TypeVar, Union, cast

import httpx
from xrpl.clients import JsonRpcClient
from xrpl.models import IssuedCurrency, XChainBridge

from sidechain_cli.exceptions import SidechainCLIException
from sidechain_cli.utils.rippled_config import RippledConfig
from sidechain_cli.utils.types import Currency, ServerData

_HOME = str(Path.home())

CONFIG_FOLDER = os.path.join(_HOME, ".config", "sidechain-cli")

# ~/.config/sidechain-cli/config.json
_CONFIG_FILE = os.path.join(CONFIG_FOLDER, "config.json")

# Initialize config file
Path(CONFIG_FOLDER).mkdir(parents=True, exist_ok=True)
if not os.path.exists(_CONFIG_FILE):
    with open(_CONFIG_FILE, "w") as f:
        data: Dict[str, Any] = {"chains": [], "witnesses": [], "bridges": []}
        json.dump(data, f, indent=4)

# TODO: consider having separate JSONs for each node type
# (e.g. chains.json, witnesses.json, bridges.json)


def get_config_folder() -> str:
    """
    Get the folder in which all of the CLI config data is located.

    Returns:
        The full name of the config folder.
    """
    return CONFIG_FOLDER


T = TypeVar("T", bound="ConfigItem")


class ConfigItem(ABC):
    """Abstract class representing a config item."""

    @classmethod
    def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        """
        Convert a dictionary to a given config object.

        Args:
            data: The dictionary to convert.

        Returns:
            The associated config object.
        """
        return cls(**data)


@dataclass
class ServerConfig(ConfigItem):
    """Object representing the config for a server (chain/witness)."""

    name: str
    type: Union[Literal["rippled"], Literal["witness"]]
    pid: int
    exe: str
    config: str
    http_ip: str
    http_port: int

    def is_docker(self: ServerConfig) -> bool:
        """
        Return whether the server is running on docker.

        Returns:
            Whether the server is running on docker.
        """
        return self.exe == "docker"


@dataclass
class ChainConfig(ServerConfig):
    """Object representing the config for a chain."""

    ws_ip: str
    ws_port: int

    @property
    def rippled(self: ChainConfig) -> str:
        """
        Get the rippled executable. Alias for `self.exe`.

        Returns:
            `self.exe`.
        """
        return self.exe

    def get_client(self: ChainConfig) -> JsonRpcClient:
        """
        Get a client connected to the chain. Requires that the chain be running.

        Returns:
            A JsonRpcClient that is connected to this chain.
        """
        return JsonRpcClient(f"http://{self.http_ip}:{self.http_port}")

    def get_config(self: ChainConfig) -> RippledConfig:
        """
        Get the config file for this chain.

        Returns:
            The RippledConfig object for this config file.
        """
        return RippledConfig(file_name=self.config)


@dataclass
class WitnessConfig(ServerConfig):
    """Object representing the config for a witness."""

    @property
    def witnessd(self: WitnessConfig) -> str:
        """
        Get the witnessd executable. Alias for `self.exe`.

        Returns:
            `self.exe`.
        """
        return self.exe

    def get_config(self: WitnessConfig) -> Dict[str, Any]:
        """
        Get the config file for this witness.

        Returns:
            The JSON dictionary for this config file.
        """
        with open(self.config) as f:
            return cast(Dict[str, Any], json.load(f))


def _to_issued_currency(
    xchain_currency: Union[Literal["XRP"], Currency]
) -> Union[Literal["XRP"], IssuedCurrency]:
    return (
        cast(Literal["XRP"], "XRP")
        if xchain_currency == "XRP"
        else IssuedCurrency.from_dict(cast(Dict[str, Any], xchain_currency))
    )


@dataclass
class BridgeConfig(ConfigItem):
    """Object representing the config for a bridge."""

    name: str
    chains: Tuple[str, str]
    num_witnesses: int
    door_accounts: Tuple[str, str]
    xchain_currencies: Tuple[Currency, Currency]
    signature_reward: str
    create_account_amounts: Tuple[str, str]

    def get_clients(self: BridgeConfig) -> Tuple[JsonRpcClient, JsonRpcClient]:
        """
        Get the clients for the chains associated with the bridge.

        Returns:
            The clients for the chains associated with the bridge.
        """
        return (JsonRpcClient(self.chains[0]), JsonRpcClient(self.chains[1]))

    def get_bridge(self: BridgeConfig) -> XChainBridge:
        """
        Get the XChainBridge object associated with the bridge.

        Returns:
            The XChainBridge object.
        """
        locking_chain_issue = _to_issued_currency(self.xchain_currencies[0])
        issuing_chain_issue = _to_issued_currency(self.xchain_currencies[1])
        return XChainBridge(
            locking_chain_door=self.door_accounts[0],
            locking_chain_issue=locking_chain_issue,
            issuing_chain_door=self.door_accounts[1],
            issuing_chain_issue=issuing_chain_issue,
        )

    def to_xrpl(self: BridgeConfig) -> Dict[str, Any]:
        """
        Get the XRPL-formatted dictionary for the XChainBridge object.

        Returns:
            The XRPL-formatted dictionary for the XChainBridge object.
        """
        locking_chain_issue = _to_issued_currency(self.xchain_currencies[0])
        issuing_chain_issue = _to_issued_currency(self.xchain_currencies[1])
        return {
            "LockingChainDoor": self.door_accounts[0],
            "LockingChainIssue": locking_chain_issue,
            "IssuingChainDoor": self.door_accounts[1],
            "IssuingChainIssue": issuing_chain_issue,
        }


S = TypeVar("S", bound="ServerData")


def _get_running_processes(servers: List[S]) -> List[S]:
    return_list = []
    for server in servers:
        http_url = f"http://{server['http_ip']}:{server['http_port']}"
        try:
            request = {"method": "server_info"}
            httpx.post(http_url, json=request)
            return_list.append(server)
            continue
        except (
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
            httpx.WriteError,
        ):
            continue
    return return_list


class ConfigFile:
    """Helper class for working with the config file."""

    def __init__(self: ConfigFile, data: Dict[str, Any]) -> None:
        """
        Initialize a ConfigFile object.

        Args:
            data: The dictionary with the config data.
        """
        self.chains = [
            ChainConfig.from_dict(chain)
            for chain in _get_running_processes(data["chains"])
        ]
        self.witnesses = [
            WitnessConfig.from_dict(witness)
            for witness in _get_running_processes(data["witnesses"])
        ]
        self.bridges = [BridgeConfig.from_dict(bridge) for bridge in data["bridges"]]
        self.write_to_file()

    @classmethod
    def from_file(cls: Type[ConfigFile]) -> ConfigFile:
        """
        Initialize a ConfigFile object from a JSON file.

        Returns:
            The ConfigFile object.
        """
        with open(_CONFIG_FILE) as f:
            data = json.load(f)
            return cls(data)

    def get_chain(self: ConfigFile, name: str) -> ChainConfig:
        """
        Get the chain corresponding to the name.

        Args:
            name: The name of the chain.

        Returns:
            The ChainConfig object corresponding to that chain.

        Raises:
            SidechainCLIException: if there is no chain with that name.
        """
        for chain in self.chains:
            if chain.name == name:
                return chain
        raise SidechainCLIException(f"No chain with name {name}.")

    def get_witness(self: ConfigFile, name: str) -> WitnessConfig:
        """
        Get the witness corresponding to the name.

        Args:
            name: The name of the witness.

        Returns:
            The WitnessConfig object corresponding to that witness.

        Raises:
            SidechainCLIException: if there is no witness with that name.
        """
        for witness in self.witnesses:
            if witness.name == name:
                return witness
        raise SidechainCLIException(f"No witness with name {name}.")

    def get_server(self: ConfigFile, name: str) -> ServerConfig:
        """
        Get the server corresponding to the name.

        Args:
            name: The name of the server.

        Returns:
            The ServerConfig object corresponding to that server.

        Raises:
            SidechainCLIException: if there is no server with that name.
        """
        for chain in self.chains:
            if chain.name == name:
                return chain
        for witness in self.witnesses:
            if witness.name == name:
                return witness
        raise SidechainCLIException(f"No server with name {name}.")

    def get_bridge(self: ConfigFile, name: str) -> BridgeConfig:
        """
        Get the bridge corresponding to the name.

        Args:
            name: The name of the bridge.

        Returns:
            The BridgeConfig object corresponding to that bridge.

        Raises:
            SidechainCLIException: if there is no bridge with that name.
        """
        for bridge in self.bridges:
            if bridge.name == name:
                return bridge
        raise SidechainCLIException(f"No bridge with name {name}.")

    def to_dict(self: ConfigFile) -> Dict[str, List[Dict[str, Any]]]:
        """
        Convert a ConfigFile object back to a dictionary.

        Returns:
            A dictionary representing the data in the object.
        """
        return {
            "chains": [asdict(chain) for chain in self.chains],
            "witnesses": [asdict(witness) for witness in self.witnesses],
            "bridges": [asdict(bridge) for bridge in self.bridges],
        }

    def write_to_file(self: ConfigFile) -> None:
        """Write the ConfigFile data to file."""
        with open(_CONFIG_FILE, "w") as f:
            json.dump(self.to_dict(), f, indent=4)
