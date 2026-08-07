"""Pin the package's public export policy.

Policy: anything named in ``polymarket/API_REFERENCE.md`` is importable from
the package root. These checks keep ``__all__`` honest so a new public model or
exception cannot land importable only through a submodule.
"""

import builtins
from pathlib import Path

import polymarket
from polymarket import exceptions as exceptions_module
from polymarket import models as models_module


def test_root_all_is_sorted_within_groups_and_unique():
    """Root __all__ must not repeat a name."""
    assert len(polymarket.__all__) == len(set(polymarket.__all__))


def test_every_root_export_resolves():
    """Every name promised by __all__ must actually be importable."""
    unresolved = [name for name in polymarket.__all__ if not hasattr(polymarket, name)]
    assert unresolved == []


def test_every_public_exception_is_exported_at_the_root():
    for name in exceptions_module.__all__:
        assert name in polymarket.__all__, f"{name} is not exported from polymarket"
        assert getattr(polymarket, name) is getattr(exceptions_module, name)


def test_every_public_model_is_exported_at_the_root():
    for name in models_module.__all__:
        assert name in polymarket.__all__, f"{name} is not exported from polymarket"
        assert getattr(polymarket, name) is getattr(models_module, name)


def test_exception_all_covers_every_exception_class():
    """No exception class may exist outside the declared public taxonomy."""
    declared = set(exceptions_module.__all__)
    defined = {
        name
        for name, obj in vars(exceptions_module).items()
        if isinstance(obj, type)
        and issubclass(obj, BaseException)
        and obj.__module__ == exceptions_module.__name__
    }
    assert defined - declared == set()


def test_timeout_error_is_catchable_as_builtin_timeout_error():
    """A caller's plain `except TimeoutError:` must catch a client timeout."""
    assert issubclass(polymarket.TimeoutError, builtins.TimeoutError)
    assert issubclass(polymarket.TimeoutError, polymarket.PolymarketError)

    try:
        raise polymarket.TimeoutError("Request timeout: boom")
    except builtins.TimeoutError as caught:
        assert str(caught) == "Request timeout: boom"
        assert caught.message == "Request timeout: boom"
    else:  # pragma: no cover - defensive
        raise AssertionError("polymarket.TimeoutError was not caught")


def test_package_ships_pep561_marker():
    marker = Path(polymarket.__file__).parent / "py.typed"
    assert marker.is_file()
