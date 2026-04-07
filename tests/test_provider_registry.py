import unittest

from decision_room.providers import ProviderConfig, ProviderRegistry


class ProviderRegistryTests(unittest.TestCase):
    def test_registry_returns_known_supplier(self) -> None:
        registry = ProviderRegistry.from_openai_compatible_configs(
            {
                "glm": ProviderConfig(
                    supplier="glm",
                    base_url="https://example.com/v1",
                    api_key="test",
                )
            }
        )
        provider = registry.get("glm")
        self.assertIsNotNone(provider)

    def test_registry_raises_for_unknown_supplier(self) -> None:
        registry = ProviderRegistry.from_openai_compatible_configs({})
        with self.assertRaises(KeyError):
            registry.get("unknown")


if __name__ == "__main__":
    unittest.main()
