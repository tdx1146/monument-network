"""
测试信封格式（monument-exchange-v1 envelope）

测试覆盖：
  - create_envelope() 正确创建信封
  - parse_envelope() 正确解析信封
  - parse_envelope() 向后兼容（直接格式）
  - /monument/sync 端点接收信封格式
  - /monument/query 端点返回信封格式
  - 信封 bootstrap_nodes 列表正确传递
"""

import sys
import os
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── 单元测试：信封创建与解析 ─────────────────────────────

from core.monument_sync import create_envelope, parse_envelope


class TestCreateEnvelope(unittest.TestCase):
    """测试 create_envelope()"""

    def test_basic_envelope_creation(self):
        """测试基本信封创建"""
        monument_data = {
            "protocol": "monument-exchange-v1",
            "from_peer": "test-peer-id",
            "monuments": [{"id": "m1", "content": "test"}],
            "signature": "abc123"
        }
        bootstrap_nodes = ["192.168.0.149:9000", "192.168.0.150:9000"]
        repo_url = "https://github.com/xxx/monument-network.git"

        envelope = create_envelope(monument_data, bootstrap_nodes, repo_url)

        # 外层结构
        self.assertIn("monument", envelope)
        self.assertIn("envelope", envelope)

        # monument 部分
        self.assertEqual(envelope["monument"], monument_data)

        # envelope 元信息
        env = envelope["envelope"]
        self.assertEqual(env["protocol"], "monument-exchange-v1")
        self.assertEqual(env["network_id"], "monument-v1")
        self.assertEqual(env["bootstrap_nodes"], bootstrap_nodes)
        self.assertEqual(env["repo_url"], repo_url)
        self.assertEqual(env["min_version"], "v1.4.0")

    def test_envelope_with_empty_bootstrap_nodes(self):
        """测试空的 bootstrap_nodes"""
        envelope = create_envelope({"id": "test"}, [], "")
        self.assertEqual(envelope["envelope"]["bootstrap_nodes"], [])

    def test_envelope_structure(self):
        """验证信封结构完整性"""
        envelope = create_envelope(
            {"id": "test"},
            ["node1:9000"],
            "https://example.com/repo.git"
        )
        expected_keys = {"monument", "envelope"}
        self.assertEqual(set(envelope.keys()), expected_keys)

        expected_envelope_keys = {
            "protocol", "network_id", "bootstrap_nodes",
            "repo_url", "min_version"
        }
        self.assertEqual(set(envelope["envelope"].keys()), expected_envelope_keys)


class TestParseEnvelope(unittest.TestCase):
    """测试 parse_envelope()"""

    def test_parse_envelope_format(self):
        """测试解析信封格式"""
        envelope_data = {
            "monument": {
                "protocol": "monument-exchange-v1",
                "from_peer": "test-peer",
                "monuments": [{"id": "m1"}],
            },
            "envelope": {
                "protocol": "monument-exchange-v1",
                "network_id": "monument-v1",
                "bootstrap_nodes": ["192.168.0.149:9000"],
                "repo_url": "https://github.com/xxx/monument-network.git",
                "min_version": "v1.4.0"
            }
        }

        monument, envelope = parse_envelope(envelope_data)

        self.assertEqual(monument["from_peer"], "test-peer")
        self.assertEqual(monument["monuments"][0]["id"], "m1")
        self.assertEqual(envelope["network_id"], "monument-v1")
        self.assertEqual(envelope["bootstrap_nodes"], ["192.168.0.149:9000"])

    def test_parse_direct_format(self):
        """测试解析直接格式（向后兼容）"""
        direct_data = {
            "protocol": "monument-exchange-v1",
            "from_peer": "test-peer",
            "monuments": [{"id": "m1"}],
            "signature": "abc"
        }

        monument, envelope = parse_envelope(direct_data)

        # 应该正常提取 monument
        self.assertEqual(monument["from_peer"], "test-peer")
        self.assertEqual(monument["monuments"][0]["id"], "m1")
        # envelope 应为空字典
        self.assertEqual(envelope, {})

    def test_parse_empty_envelope(self):
        """测试解析空数据"""
        monument, envelope = parse_envelope({})
        self.assertEqual(monument, {})
        self.assertEqual(envelope, {})

    def test_parse_roundtrip(self):
        """测试创建→解析往返一致性"""
        original_monument = {
            "protocol": "monument-exchange-v1",
            "from_peer": "roundtrip-peer",
            "monuments": [{"id": "rt-1", "content": "roundtrip test"}],
            "timestamp": "2026-07-13T00:00:00Z"
        }
        bootstrap = ["node-a:9000", "node-b:9000"]
        repo = "https://github.com/xxx/monument-network.git"

        envelope = create_envelope(original_monument, bootstrap, repo)
        restored_monument, restored_envelope = parse_envelope(envelope)

        self.assertEqual(restored_monument, original_monument)
        self.assertEqual(restored_envelope["bootstrap_nodes"], bootstrap)
        self.assertEqual(restored_envelope["repo_url"], repo)


class TestBootstrapNodesConsistency(unittest.TestCase):
    """测试 bootstrap_nodes 一致性"""

    def test_bootstrap_nodes_preserved(self):
        """创建和解析时 bootstrap_nodes 保持一致"""
        nodes = ["192.168.0.149:9000", "192.168.1.100:9000"]
        envelope = create_envelope(
            {"id": "test"},
            nodes,
            "https://example.com/repo.git"
        )
        _, parsed = parse_envelope(envelope)
        self.assertEqual(parsed["bootstrap_nodes"], nodes)

    def test_bootstrap_nodes_empty_consistency(self):
        """空 bootstrap_nodes 的一致性"""
        envelope = create_envelope({"id": "test"}, [], "https://example.com/repo.git")
        _, parsed = parse_envelope(envelope)
        self.assertEqual(parsed["bootstrap_nodes"], [])

    def test_bootstrap_nodes_are_strings(self):
        """bootstrap_nodes 中的元素应为字符串"""
        nodes = ["192.168.0.149:9000", "10.0.0.1:18891"]
        envelope = create_envelope({"id": "test"}, nodes, "")
        for node in envelope["envelope"]["bootstrap_nodes"]:
            self.assertIsInstance(node, str)
            self.assertIn(":", node)


# ─── 集成测试：API 端点信封支持 ───────────────────────────

class TestSyncEndpointEnvelope(unittest.TestCase):
    """测试 /monument/sync 端点对信封格式的支持"""

    def setUp(self):
        from api.monument_routes import register_monument_routes as register
        from flask import Flask

        self.app = Flask(__name__)
        register(self.app)
        self.client = self.app.test_client()

    @patch("api.monument_routes.verify_monument_message")
    @patch("api.monument_routes.import_monument_json")
    def test_receive_envelope_format(self, mock_import, mock_verify):
        """测试 /sync 接收信封格式"""
        mock_verify.return_value = (True, "")
        mock_import.return_value = {
            "status": "success",
            "monuments_imported": 1,
            "message": "ok"
        }

        # 发送信封格式请求
        envelope_data = {
            "monument": {
                "protocol": "monument-exchange-v1",
                "from_peer": "peer-123",
                "monuments": [{"id": "m1", "content": "test"}],
                "signature": "sig-abc"
            },
            "envelope": {
                "protocol": "monument-exchange-v1",
                "network_id": "monument-v1",
                "bootstrap_nodes": ["192.168.0.149:9000"],
                "repo_url": "https://github.com/xxx/monument-network.git",
                "min_version": "v1.4.0"
            }
        }

        response = self.client.post(
            "/monument/sync",
            json=envelope_data,
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        # 返回的信封格式
        self.assertIn("monument", data)
        self.assertIn("envelope", data)
        self.assertEqual(data["monument"]["status"], "success")

    @patch("api.monument_routes.verify_monument_message")
    @patch("api.monument_routes.import_monument_json")
    def test_receive_direct_format_backward_compatible(self, mock_import, mock_verify):
        """测试 /sync 仍然接收直接格式（向后兼容）"""
        mock_verify.return_value = (True, "")
        mock_import.return_value = {
            "status": "success",
            "monuments_imported": 2,
            "message": "ok"
        }

        # 发送直接格式（旧的协议格式）
        direct_data = {
            "protocol": "monument-exchange-v1",
            "from_peer": "peer-456",
            "monuments": [
                {"id": "m1", "content": "test1"},
                {"id": "m2", "content": "test2"},
            ],
            "signature": "sig-xyz"
        }

        response = self.client.post(
            "/monument/sync",
            json=direct_data,
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        # 也返回信封格式
        self.assertIn("envelope", data)

    def test_receive_invalid_json(self):
        """测试非 JSON 请求"""
        response = self.client.post(
            "/monument/sync",
            data="not json",
            content_type="text/plain"
        )
        self.assertEqual(response.status_code, 400)

    def test_receive_protocol_mismatch_envelope(self):
        """测试信封中协议版本不匹配"""
        envelope_data = {
            "monument": {
                "protocol": "monument-exchange-v2",  # 不匹配的版本
                "monuments": [{"id": "m1"}],
            },
            "envelope": {
                "protocol": "monument-exchange-v2",  # 不匹配
            }
        }

        response = self.client.post(
            "/monument/sync",
            json=envelope_data,
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        data = response.get_json()
        self.assertIn("协议版本不匹配", data["message"])


class TestQueryEndpointEnvelope(unittest.TestCase):
    """测试 /monument/query 端点返回信封格式"""

    def setUp(self):
        from api.monument_routes import register_monument_routes as register
        from flask import Flask

        self.app = Flask(__name__)
        register(self.app)
        self.client = self.app.test_client()

    @patch("api.monument_routes.sign_monument_message")
    @patch("api.monument_routes.IndividualRepository")
    @patch("api.monument_routes._get_identity")
    def test_query_returns_envelope(
        self, mock_identity, mock_repo_class, mock_sign
    ):
        """测试查询返回信封格式"""
        identity_mock = MagicMock()
        identity_mock.peer_id = "test-peer-id"
        mock_identity.return_value = identity_mock

        repo_mock = MagicMock()
        repo_mock.list_all.return_value = []
        mock_repo_class.return_value = repo_mock
        mock_sign.side_effect = lambda x, y: x  # passthrough

        # 模拟 _ensure_repos 返回 mock
        with patch("api.monument_routes._ensure_repos") as mock_repos:
            mock_repos.return_value = (repo_mock, MagicMock())

            response = self.client.get("/monument/query")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()

            # 应返回信封格式
            self.assertIn("envelope", data)
            self.assertIn("monument", data)
            self.assertEqual(
                data["envelope"]["protocol"],
                "monument-exchange-v1"
            )


class TestEnvelopeIntegration(unittest.TestCase):
    """完整的信封往返测试"""

    def test_full_envelope_workflow(self):
        """测试完整工作流：创建信封 → 解析 → 提取 monument"""
        # 创建
        original = {
            "protocol": "monument-exchange-v1",
            "from_peer": "integration-peer",
            "monuments": [
                {"id": "i1", "type": "draft", "content": "集成测试"}
            ],
            "timestamp": "2026-07-13T12:00:00Z"
        }
        nodes = ["192.168.0.149:9000"]
        envelope = create_envelope(original, nodes, "https://repo.git")

        # 序列化（模拟网络传输）
        serialized = json.dumps(envelope)
        deserialized = json.loads(serialized)

        # 解析
        monument, meta = parse_envelope(deserialized)

        # 验证
        self.assertEqual(monument, original)
        self.assertEqual(meta["bootstrap_nodes"], nodes)
        self.assertEqual(meta["protocol"], "monument-exchange-v1")


if __name__ == "__main__":
    unittest.main()
