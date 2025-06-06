"""Utility functions to fetch data from the database in bulk"""

from typing import List, Union

import pandas as pd
from sqlalchemy.dialects import sqlite
from sqlalchemy import text

from mendeleev import get_all_elements
from mendeleev.electronegativity import allred_rochow, gordy, cottrell_sutton

from .db import get_engine, get_session
from .models import Element, IonizationEnergy


def fetch_table(table: str, **kwargs) -> pd.DataFrame:
    """
    Return a table from the database as :py:class:`pandas.DataFrame`

    Args:
        table: Name of the table from the database
        kwargs: A dictionary of keyword arguments to pass to the :py:func:`pandas.read_qsl`

    Returns:
        df (pandas.DataFrame): Pandas DataFrame with the contents of the table

    Example:
        >>> from mendeleev.fetch import fetch_table
        >>> df = fetch_table('elements')
        >>> type(df)
        pandas.core.frame.DataFrame

    """

    tables = {
        "elements",
        "groups",
        "ionicradii",
        "ionizationenergies",
        "isotopedecaymodes",
        "isotopes",
        "oxidationstates",
        "phasetransitions",
        "propertymetadata",
        "scattering_factors",
        "screeningconstants",
        "series",
    }

    if table not in tables:
        raise ValueError(
            f"Table '{table}' not found, available tables are: {', '.join(sorted(tables))}"
        )

    engine = get_engine()
    query = f"SELECT * FROM {table}"
    with engine.begin() as conn:
        return pd.read_sql_query(sql=text(query), con=conn, **kwargs)


def fetch_electronegativities(scales: List[str] = None) -> pd.DataFrame:
    """
    Fetch electronegativity scales for all elements as :py:class:`pandas.DataFrame`

    Args:
        scales: list of scale names, defaults to all available scales

    Returns:
        df (pandas.DataFrame): Pandas DataFrame with the contents of the table
    """
    scales = [
        "li-xue",
        "martynov-batsanov",
        "mulliken",
        "nagle",
        "sanderson",
    ]

    session = get_session()
    engine = get_engine()

    query = session.query(
        Element.atomic_number,
        Element.symbol,
        Element.covalent_radius_pyykko.label("radius"),
        Element.en_allen.label("Allen"),
        Element.en_ghosh.label("Ghosh"),
        Element.en_gunnarsson_lundqvist.label("Gunnarsson-Lundqvist"),
        Element.en_miedema.label("Miedema"),
        Element.en_mullay.label("Mullay"),
        Element.en_pauling.label("Pauling"),
        Element.en_robles_bartolotti.label("Robles-Bartolotti"),
    ).order_by("atomic_number")
    df = pd.read_sql_query(query.statement.compile(dialect=sqlite.dialect()), engine)

    elems = get_all_elements()

    df.loc[:, "zeff"] = [e.zeff() for e in elems]
    # scales
    df.loc[:, "Allred-Rochow"] = allred_rochow(df["zeff"], df["radius"])
    df.loc[:, "Cottrell-Sutton"] = cottrell_sutton(df["zeff"], df["radius"])
    df.loc[:, "Gordy"] = gordy(df["zeff"], df["radius"])

    for scale in scales:
        scale_name = "-".join(s.capitalize() for s in scale.split("-"))
        df.loc[:, scale_name] = [e.electronegativity(scale=scale) for e in elems]
    return df.set_index("atomic_number")


def fetch_ionization_energies(degree: Union[List[int], int] = 1) -> pd.DataFrame:
    """
    Fetch a :py:class:`pandas.DataFrame` with ionization energies for all elements
    indexed by atomic number.

    Args:
        degree: Degree of ionization, either as int or a list of ints. If a list is
            passed then the output will contain ionization energies corresponding
            to particalr degrees in columns.

    Returns:
        df (pandas.DataFrame): ionization energies, indexed by atomic number
    """

    # validate degree
    if isinstance(degree, (list, tuple, set)):
        if not all(isinstance(d, int) for d in degree) or any(d <= 0 for d in degree):
            raise ValueError("degree should be a list of positive ints")
    elif isinstance(degree, int):
        if degree <= 0:
            raise ValueError("degree should be positive")
        degree = [degree]
    else:
        raise ValueError(
            f"degree should be either a positive int or a collection of positive ints, got: {degree}"
        )

    session = get_session()
    engine = get_engine()

    query = session.query(Element.atomic_number).order_by("atomic_number")
    df = pd.read_sql_query(query.statement.compile(dialect=sqlite.dialect()), engine)

    for d in degree:
        query = session.query(IonizationEnergy).filter(IonizationEnergy.degree == d)
        energies = pd.read_sql_query(
            query.statement.compile(dialect=sqlite.dialect()), engine
        )

        df = pd.merge(
            df,
            energies.loc[:, ["atomic_number", "ionization_energy"]].rename(
                columns={"ionization_energy": "IE{0:d}".format(d)}
            ),
            on="atomic_number",
            how="left",
        )

    return df.set_index("atomic_number")


def fetch_neutral_data() -> pd.DataFrame:
    """
    Get extensive set of data from multiple database tables as pandas.DataFrame
    """

    elements = fetch_table("elements")
    series = fetch_table("series")
    groups = fetch_table("groups")

    elements = pd.merge(
        elements,
        series,
        left_on="series_id",
        right_on="id",
        how="left",
        suffixes=("", "_series"),
    )
    elements = pd.merge(
        elements,
        groups,
        left_on="group_id",
        right_on="group_id",
        how="left",
        suffixes=("", "_group"),
    )

    elements.rename(columns={"color": "series_colors"}, inplace=True)
    # get all element objects
    ELEMS = get_all_elements()
    elements.loc[:, "hardness"] = [e.hardness() for e in ELEMS]
    elements.loc[:, "softness"] = [e.softness() for e in ELEMS]
    elements.loc[:, "mass"] = [e.mass_str() for e in ELEMS]
    elements.loc[:, "zeff_slater"] = [e.zeff(method="slater") for e in ELEMS]
    elements.loc[:, "zeff_clementi"] = [e.zeff(method="clementi") for e in ELEMS]

    ens = fetch_electronegativities()
    elements = pd.merge(elements, ens.reset_index(), on="atomic_number", how="left")

    ies = fetch_ionization_energies(degree=1)
    elements = pd.merge(elements, ies.reset_index(), on="atomic_number", how="left")

    return elements


def fetch_ionic_radii(radius: str = "ionic_radius") -> pd.DataFrame:
    """
    Fetch a pandas DataFrame with ionic radii for all the elements.

    Args:
        radius: The radius to be returned either `ionic_radius` or `crystal_radius`

    Returns:
        df (pandas.DataFrame): a table with atomic numbers, symbols and ionic radii for all
            coordination numbers
    """
    if radius not in ["ionic_radius", "crystal_radius"]:
        raise ValueError(
            "radius '{radius}', not found, available radii are: 'ionic_radius', 'crystal_radius'"
        )

    ir = fetch_table("ionicradii")
    return ir.pivot_table(
        columns="coordination", values=radius, index=["atomic_number", "charge"]
    )
