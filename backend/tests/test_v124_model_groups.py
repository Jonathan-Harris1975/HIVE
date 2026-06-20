from app.core.config import Settings
from app.services.model_router import ModelRouter


def test_image_generation_model_is_discovery_only() -> None:
    router = ModelRouter(Settings())
    summary = router.summarise_model(
        {
            "id": "example/image-maker",
            "name": "Image Maker",
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["image"],
            },
            "pricing": {"prompt": "0.0001", "completion": "0"},
        }
    )

    assert summary["primary_group"] == "image_generation"
    assert summary["chat_selectable"] is False
    assert "Discovery-only" in summary["disabled_reason"]


def test_video_generation_model_is_discovery_only() -> None:
    router = ModelRouter(Settings())
    summary = router.summarise_model(
        {
            "id": "example/video-maker",
            "architecture": {
                "input_modalities": ["text", "image"],
                "output_modalities": ["video"],
            },
            "pricing": {"request": "0.1"},
        }
    )

    assert summary["primary_group"] == "video_generation"
    assert summary["chat_selectable"] is False
    assert "creation workspace" in summary["disabled_reason"]


def test_configured_free_model_has_roles_and_free_flag() -> None:
    settings = Settings(
        default_model="example/free-chat:free",
        cheap_model="example/free-chat:free",
    )
    router = ModelRouter(settings)
    summary = router.summarise_model(
        {
            "id": "example/free-chat:free",
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["text"],
            },
            "pricing": {"prompt": "0", "completion": "0"},
        }
    )

    assert summary["is_free"] is True
    assert summary["configured_roles"] == ["default", "cheap"]
    assert summary["primary_group"] == "configured"
    assert summary["chat_selectable"] is True


def test_vision_and_document_groups_are_classified() -> None:
    router = ModelRouter(Settings())
    summary = router.summarise_model(
        {
            "id": "example/vision-long",
            "architecture": {
                "input_modalities": ["text", "image", "file"],
                "output_modalities": ["text"],
            },
            "context_length": 200_000,
            "pricing": {"prompt": "0.1", "completion": "0.2"},
        }
    )

    assert "vision" in summary["groups"]
    assert "documents" in summary["groups"]
    assert summary["chat_selectable"] is True


def test_free_image_model_stays_in_image_generation_group() -> None:
    router = ModelRouter(Settings())
    summary = router.summarise_model(
        {
            "id": "example/free-image:free",
            "architecture": {
                "input_modalities": ["text"],
                "output_modalities": ["image"],
            },
            "pricing": {"prompt": "0", "completion": "0"},
        }
    )

    assert summary["is_free"] is True
    assert "free" in summary["groups"]
    assert summary["primary_group"] == "image_generation"
    assert summary["chat_selectable"] is False
