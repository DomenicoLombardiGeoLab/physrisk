import os
from abc import ABC
from pathlib import PosixPath
from typing import Dict, List, MutableMapping, Optional

from typing_extensions import Protocol

from physrisk.data.inventory import Inventory
from physrisk.kernel import hazards

from .zarr_reader import ZarrReader


class SourcePath(Protocol):
    """Provides path to hazard event data source. Each source should have its own implementation.
    Args:
        model: model identifier.
        scenario: identifier of scenario, e.g. rcp8p5 (RCP 8.5).
        year: projection year, e.g. 2080.
    """

    def __call__(self, *, model: str, scenario: str, year: int) -> str:
        ...


class HazardDataProvider(ABC):
    def __init__(
        self,
        get_source_path: SourcePath,
        *,
        store: Optional[MutableMapping] = None,
        zarr_reader: Optional[ZarrReader] = None,
        interpolation: Optional[str] = "floor",
    ):
        """Create an EventProvider.

        Args:
            get_source_path: provides the path to the hazard event data source depending on year/scenario/model.
        """
        self._get_source_path = get_source_path
        self._reader = zarr_reader if zarr_reader is not None else ZarrReader(store=store)
        if interpolation not in ["floor", "linear"]:
            raise ValueError("interpolation must be 'floor' or 'linear'")
        self._interpolation = interpolation


class AcuteHazardDataProvider(HazardDataProvider):
    """Provides hazard event intensities for a single Hazard (type of hazard event)."""

    def __init__(
        self,
        get_source_path: SourcePath,
        *,
        store: Optional[MutableMapping] = None,
        zarr_reader: Optional[ZarrReader] = None,
        interpolation: Optional[str] = "floor",
    ):
        super().__init__(get_source_path, store=store, zarr_reader=zarr_reader, interpolation=interpolation)

    def get_intensity_curves(
        self, longitudes: List[float], latitudes: List[float], *, model: str, scenario: str, year: int
    ):
        """Get intensity curve for each latitude and longitude coordinate pair.

        Args:
            longitudes: list of longitudes.
            latitudes: list of latitudes.
            model: model identifier.
            scenario: identifier of scenario, e.g. rcp8p5 (RCP 8.5).
            year: projection year, e.g. 2080.

        Returns:
            curves: numpy array of intensity (no. coordinate pairs, no. return periods).
            return_periods: return periods in years.
        """

        path = self._get_source_path(model=model, scenario=scenario, year=year)
        curves, return_periods = self._reader.get_curves(
            path, longitudes, latitudes, self._interpolation
        )  # type: ignore
        return curves, return_periods


class ChronicHazardDataProvider(HazardDataProvider):
    """Provides hazard parameters for a single type of chronic hazard."""

    def __init__(
        self,
        get_source_path: SourcePath,
        *,
        store: Optional[MutableMapping] = None,
        zarr_reader: Optional[ZarrReader] = None,
        interpolation: Optional[str] = "floor",
    ):
        super().__init__(get_source_path, store=store, zarr_reader=zarr_reader, interpolation=interpolation)

    def get_parameters(self, longitudes: List[float], latitudes: List[float], *, model: str, scenario: str, year: int):
        """Get hazard parameters for each latitude and longitude coordinate pair.

        Args:
            longitudes: list of longitudes.
            latitudes: list of latitudes.
            model: model identifier.
            scenario: identifier of scenario, e.g. rcp8p5 (RCP 8.5).
            year: projection year, e.g. 2080.

        Returns:
            parameters: numpy array of parameters
        """

        path = self._get_source_path(model=model, scenario=scenario, year=year)
        parameters, _ = self._reader.get_curves(path, longitudes, latitudes, self._interpolation)
        return parameters[:, 0]


# region World Resource Aqueduct Model


def _wri_inundation_prefix():
    return "inundation/wri/v2"


_percentiles_map = {"95": "0", "5": "0_perc_05", "50": "0_perc_50"}
_subsidence_set = {"wtsub", "nosub"}


def get_source_path_wri_coastal_inundation(*, model: str, scenario: str, year: int):
    type = "coast"
    # model is expected to be of the form subsidence/percentile, e.g. wtsub/95
    # if percentile is omitted then 95th percentile is used
    model_components = model.split("/")
    sub = model_components[0]
    if sub not in _subsidence_set:
        raise ValueError("expected model input of the form {subsidence/percentile}, e.g. wtsub/95, nosub/5, wtsub/50")
    perc = "95" if len(model_components) == 1 else model_components[1]
    return os.path.join(
        _wri_inundation_prefix(), f"inun{type}_{cmip6_scenario_to_rcp(scenario)}_{sub}_{year}_{_percentiles_map[perc]}"
    )


def get_source_path_wri_riverine_inundation(*, model: str, scenario: str, year: int):
    type = "river"
    return os.path.join(_wri_inundation_prefix(), f"inun{type}_{cmip6_scenario_to_rcp(scenario)}_{model}_{year}")


def cmip6_scenario_to_rcp(scenario: str):
    """Convention is that CMIP6 scenarios are expressed by identifiers:
    SSP1-2.6: 'ssp126'
    SSP2-4.5: 'ssp245'
    SSP5-8.5: 'ssp585' etc.
    Here we translate to form
    RCP-4.5: 'rcp4p5'
    RCP-8.5: 'rcp8p5' etc.
    """
    if scenario == "ssp126":
        return "rcp2p6"
    elif scenario == "ssp245":
        return "rcp4p5"
    elif scenario == "ssp585":
        return "rcp8p5"
    else:
        if scenario not in ["rcp2p6", "rcp4p5", "rcp8p5", "historical"]:
            raise ValueError(f"unexpected scenario {scenario}")
        return scenario


# endregion

# region OS-C Chronic Heat Model


def _osc_chronic_heat_prefix():
    return "chronic_heat/osc/v1"


def get_source_path_osc_chronic_heat(*, model: str, scenario: str, year: int):
    type, *levels = model.split("/")

    if type == "mean_degree_days":
        assert levels[0] in ["above", "below"]  # above or below
        assert levels[1] in ["18c", "32c"]  # threshold temperature
        return _osc_chronic_heat_prefix() + "/" + f"{type}_{levels[0]}_{levels[1]}_{scenario}_{year}"

    elif type == "mean_work_loss":
        assert levels[0] in ["low", "medium", "high"]  # work intensity
        return _osc_chronic_heat_prefix() + "/" + f"{type}_{levels[0]}_{scenario}_{year}"

    else:
        raise ValueError("valid types are {valid_types}")


# endregion


def get_source_path_generic(inventory: Inventory, hazard_type: str, embedded: Optional[Dict[type, SourcePath]]):
    resources_dict = dict(
        (id, resources[0])
        for ((htype, id), resources) in inventory.resources_by_type_id.items()
        if htype == hazard_type
    )

    def get_source_path(*, model: str, scenario: str, year: int):
        if model not in resources_dict:
            if embedded is None:
                return None
            return embedded[hazards.hazard_class(hazard_type)](model=model, scenario=scenario, year=year)
        resource = resources_dict[model]
        # if scenario not in [s.id for s in resource.scenarios]
        proxy_scenario = cmip6_scenario_to_rcp(scenario) if resource.scenarios[0].id.startswith("rcp") else scenario
        return str(PosixPath(resource.path, resource.array_name.format(id=model, scenario=proxy_scenario, year=year)))

    return get_source_path
