from legion.workflows.registry import WorkflowRegistry


class MockWorkflow:
    name = "test-workflow"
    description = "A test workflow"

    def compile(self, config=None):
        return "compiled_graph"


def test_register_and_get():
    registry = WorkflowRegistry()
    workflow = MockWorkflow()
    registry.register(workflow)
    assert registry.get("test-workflow") is workflow


def test_get_nonexistent():
    registry = WorkflowRegistry()
    assert registry.get("nonexistent") is None


def test_list_workflows():
    registry = WorkflowRegistry()
    registry.register(MockWorkflow())
    workflows = registry.list()
    assert len(workflows) == 1
    assert workflows[0]["name"] == "test-workflow"


def test_duplicate_registration_replaces():
    registry = WorkflowRegistry()
    w1 = MockWorkflow()
    w2 = MockWorkflow()
    registry.register(w1)
    registry.register(w2)
    assert registry.get("test-workflow") is w2
