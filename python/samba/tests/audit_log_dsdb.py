# Tests for SamDb password change audit logging.
# Copyright (C) Andrew Bartlett <abartlet@samba.org> 2018
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from __future__ import print_function
"""Tests for the SamDb logging of password changes.
"""

import samba.tests
from samba.dcerpc.messaging import MSG_DSDB_LOG, DSDB_EVENT_NAME
from samba.samdb import SamDB
from samba.auth import system_session
import os
import time
from samba.tests.audit_log_base import AuditLogTestBase
from samba.tests import delete_force
from samba.net import Net
import samba
from samba.dcerpc import security, lsa

USER_NAME = "auditlogtestuser"
USER_PASS = samba.generate_random_password(32, 32)
SECOND_USER_NAME = "auditlogtestuser02"
SECOND_USER_PASS = samba.generate_random_password(32, 32)


class AuditLogDsdbTests(AuditLogTestBase):

    def setUp(self):
        self.message_type = MSG_DSDB_LOG
        self.event_type   = DSDB_EVENT_NAME
        super(AuditLogDsdbTests, self).setUp()

        self.remoteAddress = os.environ["CLIENT_IP"]
        self.server_ip = os.environ["SERVER_IP"]

        host = "ldap://%s" % os.environ["SERVER"]
        self.ldb = SamDB(url=host,
                         session_info=system_session(),
                         credentials=self.get_credentials(),
                         lp=self.get_loadparm())
        self.server = os.environ["SERVER"]

        # Gets back the basedn
        self.base_dn = self.ldb.domain_dn()

        # Get the old "dSHeuristics" if it was set
        dsheuristics = self.ldb.get_dsheuristics()

        # Set the "dSHeuristics" to activate the correct "userPassword"
        # behaviour
        self.ldb.set_dsheuristics("000000001")

        # Reset the "dSHeuristics" as they were before
        self.addCleanup(self.ldb.set_dsheuristics, dsheuristics)

        # Get the old "minPwdAge"
        minPwdAge = self.ldb.get_minPwdAge()

        # Set it temporarily to "0"
        self.ldb.set_minPwdAge("0")
        self.base_dn = self.ldb.domain_dn()

        # Reset the "minPwdAge" as it was before
        self.addCleanup(self.ldb.set_minPwdAge, minPwdAge)

        # (Re)adds the test user USER_NAME with password USER_PASS
        delete_force(self.ldb, "cn=" + USER_NAME + ",cn=users," + self.base_dn)
        delete_force(
            self.ldb,
            "cn=" + SECOND_USER_NAME + ",cn=users," + self.base_dn)
        self.ldb.add({
            "dn": "cn=" + USER_NAME + ",cn=users," + self.base_dn,
            "objectclass": "user",
            "sAMAccountName": USER_NAME,
            "userPassword": USER_PASS
        })

    #
    # Discard the messages from the setup code
    #
    def discardSetupMessages(self, dn):
        messages = self.waitForMessages(2, dn=dn)
        self.discardMessages()


    def tearDown(self):
        self.discardMessages()
        super(AuditLogDsdbTests, self).tearDown()

    def waitForTransaction(self, connection=None):
        """Wait for a transaction message to arrive
        The connection is passed through to keep the connection alive
        until all the logging messages have been received.
        """

        self.connection = connection

        start_time = time.time()
        while self.context["txnMessage"] == "":
            self.msg_ctx.loop_once(0.1)
            if time.time() - start_time > 1:
                self.connection = None
                return ""

        self.connection = None
        return self.context["txnMessage"]

    def test_net_change_password(self):

        dn = "CN=" + USER_NAME + ",CN=Users," + self.base_dn
        self.discardSetupMessages(dn)

        creds = self.insta_creds(template=self.get_credentials())

        lp = self.get_loadparm()
        net = Net(creds, lp, server=self.server)
        password = "newPassword!!42"

        net.change_password(newpassword=password.encode('utf-8'),
                            username=USER_NAME,
                            oldpassword=USER_PASS)

        messages = self.waitForMessages(1, net, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")

        audit = messages[0]["dsdbChange"]
        self.assertEquals("Modify", audit["operation"])
        self.assertFalse(audit["performedAsSystem"])
        self.assertTrue(dn.lower(), audit["dn"].lower())
        self.assertRegexpMatches(audit["remoteAddress"],
                                 self.remoteAddress)
        session_id = self.get_session()
        self.assertEquals(session_id, audit["sessionId"])
        service_description = self.get_service_description()
        self.assertEquals(service_description, "DCE/RPC")
        self.assertTrue(self.is_guid(audit["transactionId"]))

        attributes = audit["attributes"]
        self.assertEquals(1, len(attributes))
        actions = attributes["clearTextPassword"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertTrue(actions[0]["redacted"])
        self.assertEquals("replace", actions[0]["action"])

    def test_net_set_password(self):

        dn = "CN=" + USER_NAME + ",CN=Users," + self.base_dn
        self.discardSetupMessages(dn)

        creds = self.insta_creds(template=self.get_credentials())

        lp = self.get_loadparm()
        net = Net(creds, lp, server=self.server)
        password = "newPassword!!42"
        domain = lp.get("workgroup")

        net.set_password(newpassword=password.encode('utf-8'),
                         account_name=USER_NAME,
                         domain_name=domain)
        messages = self.waitForMessages(1, net, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")
        audit = messages[0]["dsdbChange"]
        self.assertEquals("Modify", audit["operation"])
        self.assertFalse(audit["performedAsSystem"])
        self.assertEquals(dn, audit["dn"])
        self.assertRegexpMatches(audit["remoteAddress"],
                                 self.remoteAddress)
        session_id = self.get_session()
        self.assertEquals(session_id, audit["sessionId"])
        service_description = self.get_service_description()
        self.assertEquals(service_description, "DCE/RPC")
        self.assertTrue(self.is_guid(audit["transactionId"]))

        attributes = audit["attributes"]
        self.assertEquals(1, len(attributes))
        actions = attributes["clearTextPassword"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertTrue(actions[0]["redacted"])
        self.assertEquals("replace", actions[0]["action"])

    def test_ldap_change_password(self):

        dn = "cn=" + USER_NAME + ",cn=users," + self.base_dn
        self.discardSetupMessages(dn)

        new_password = samba.generate_random_password(32, 32)
        dn = "cn=" + USER_NAME + ",cn=users," + self.base_dn
        self.ldb.modify_ldif(
            "dn: " + dn + "\n" +
            "changetype: modify\n" +
            "delete: userPassword\n" +
            "userPassword: " + USER_PASS + "\n" +
            "add: userPassword\n" +
            "userPassword: " + new_password + "\n")

        messages = self.waitForMessages(1)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")

        audit = messages[0]["dsdbChange"]
        self.assertEquals("Modify", audit["operation"])
        self.assertFalse(audit["performedAsSystem"])
        self.assertEquals(dn, audit["dn"])
        self.assertRegexpMatches(audit["remoteAddress"],
                                 self.remoteAddress)
        self.assertTrue(self.is_guid(audit["sessionId"]))
        session_id = self.get_session()
        self.assertEquals(session_id, audit["sessionId"])
        service_description = self.get_service_description()
        self.assertEquals(service_description, "LDAP")

        attributes = audit["attributes"]
        self.assertEquals(1, len(attributes))
        actions = attributes["userPassword"]["actions"]
        self.assertEquals(2, len(actions))
        self.assertTrue(actions[0]["redacted"])
        self.assertEquals("delete", actions[0]["action"])
        self.assertTrue(actions[1]["redacted"])
        self.assertEquals("add", actions[1]["action"])

    def test_ldap_replace_password(self):

        dn = "cn=" + USER_NAME + ",cn=users," + self.base_dn
        self.discardSetupMessages(dn)

        new_password = samba.generate_random_password(32, 32)
        self.ldb.modify_ldif(
            "dn: " + dn + "\n" +
            "changetype: modify\n" +
            "replace: userPassword\n" +
            "userPassword: " + new_password + "\n")

        messages = self.waitForMessages(1, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")

        audit = messages[0]["dsdbChange"]
        self.assertEquals("Modify", audit["operation"])
        self.assertFalse(audit["performedAsSystem"])
        self.assertTrue(dn.lower(), audit["dn"].lower())
        self.assertRegexpMatches(audit["remoteAddress"],
                                 self.remoteAddress)
        self.assertTrue(self.is_guid(audit["sessionId"]))
        session_id = self.get_session()
        self.assertEquals(session_id, audit["sessionId"])
        service_description = self.get_service_description()
        self.assertEquals(service_description, "LDAP")
        self.assertTrue(self.is_guid(audit["transactionId"]))

        attributes = audit["attributes"]
        self.assertEquals(1, len(attributes))
        actions = attributes["userPassword"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertTrue(actions[0]["redacted"])
        self.assertEquals("replace", actions[0]["action"])

    def test_ldap_add_user(self):

        # The setup code adds a user, so we check for the dsdb events
        # generated by it.
        dn = "cn=" + USER_NAME + ",cn=users," + self.base_dn
        messages = self.waitForMessages(2, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(2,
                          len(messages),
                          "Did not receive the expected number of messages")

        audit = messages[1]["dsdbChange"]
        self.assertEquals("Add", audit["operation"])
        self.assertFalse(audit["performedAsSystem"])
        self.assertEquals(dn, audit["dn"])
        self.assertRegexpMatches(audit["remoteAddress"],
                                 self.remoteAddress)
        session_id = self.get_session()
        self.assertEquals(session_id, audit["sessionId"])
        service_description = self.get_service_description()
        self.assertEquals(service_description, "LDAP")
        self.assertTrue(self.is_guid(audit["sessionId"]))
        self.assertTrue(self.is_guid(audit["transactionId"]))

        attributes = audit["attributes"]
        self.assertEquals(3, len(attributes))

        actions = attributes["objectclass"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertEquals("add", actions[0]["action"])
        self.assertEquals(1, len(actions[0]["values"]))
        self.assertEquals("user", actions[0]["values"][0]["value"])

        actions = attributes["sAMAccountName"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertEquals("add", actions[0]["action"])
        self.assertEquals(1, len(actions[0]["values"]))
        self.assertEquals(USER_NAME, actions[0]["values"][0]["value"])

        actions = attributes["userPassword"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertEquals("add", actions[0]["action"])
        self.assertTrue(actions[0]["redacted"])

    def test_samdb_delete_user(self):

        dn = "cn=" + USER_NAME + ",cn=users," + self.base_dn
        self.discardSetupMessages(dn)

        self.ldb.deleteuser(USER_NAME)

        messages = self.waitForMessages(2, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(2,
                          len(messages),
                          "Did not receive the expected number of messages")

        audit = messages[1]["dsdbChange"]
        self.assertEquals("Delete", audit["operation"])
        self.assertFalse(audit["performedAsSystem"])
        self.assertTrue(dn.lower(), audit["dn"].lower())
        self.assertRegexpMatches(audit["remoteAddress"],
                                 self.remoteAddress)
        self.assertTrue(self.is_guid(audit["sessionId"]))
        session_id = self.get_session()
        self.assertEquals(session_id, audit["sessionId"])
        service_description = self.get_service_description()
        self.assertEquals(service_description, "LDAP")

    def test_net_set_password_user_without_permission(self):

        self.ldb.newuser(SECOND_USER_NAME, SECOND_USER_PASS)

        creds = self.insta_creds(
            template=self.get_credentials(),
            username=SECOND_USER_NAME,
            userpass=SECOND_USER_PASS,
            kerberos_state=None)

        lp = self.get_loadparm()
        net = Net(creds, lp, server=self.server)
        password = "newPassword!!42"
        domain = lp.get("workgroup")

        #
        # This operation should fail and trigger a transaction roll back.
        #
        try:
            net.set_password(newpassword=password.encode('utf-8'),
                             account_name=USER_NAME,
                             domain_name=domain)
            self.fail("Expected exception not thrown")
        except Exception:
            pass

        message = self.waitForTransaction(net)

        audit = message["dsdbTransaction"]
        self.assertEquals("rollback", audit["action"])
        self.assertTrue(self.is_guid(audit["transactionId"]))

    def test_create_and_delete_secret_over_lsa(self):

        dn = "cn=Test Secret,CN=System," + self.base_dn
        self.discardSetupMessages(dn)

        creds = self.insta_creds(template=self.get_credentials())
        lsa_conn = lsa.lsarpc(
            "ncacn_np:%s" % self.server,
            self.get_loadparm(),
            creds)
        lsa_handle = lsa_conn.OpenPolicy2(
            system_name="\\",
            attr=lsa.ObjectAttribute(),
            access_mask=security.SEC_FLAG_MAXIMUM_ALLOWED)
        secret_name = lsa.String()
        secret_name.string = "G$Test"
        lsa_conn.CreateSecret(
            handle=lsa_handle,
            name=secret_name,
            access_mask=security.SEC_FLAG_MAXIMUM_ALLOWED)

        messages = self.waitForMessages(1, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")

        audit = messages[0]["dsdbChange"]
        self.assertEquals("Add", audit["operation"])
        self.assertTrue(audit["performedAsSystem"])
        self.assertTrue(dn.lower(), audit["dn"].lower())
        self.assertRegexpMatches(audit["remoteAddress"],
                                 self.remoteAddress)
        self.assertTrue(self.is_guid(audit["sessionId"]))
        session_id = self.get_session()
        self.assertEquals(session_id, audit["sessionId"])
        service_description = self.get_service_description()
        self.assertEquals(service_description, "DCE/RPC")
        attributes = audit["attributes"]
        self.assertEquals(2, len(attributes))

        object_class = attributes["objectClass"]
        self.assertEquals(1, len(object_class["actions"]))
        action = object_class["actions"][0]
        self.assertEquals("add", action["action"])
        values = action["values"]
        self.assertEquals(1, len(values))
        self.assertEquals("secret", values[0]["value"])

        cn = attributes["cn"]
        self.assertEquals(1, len(cn["actions"]))
        action = cn["actions"][0]
        self.assertEquals("add", action["action"])
        values = action["values"]
        self.assertEquals(1, len(values))
        self.assertEquals("Test Secret", values[0]["value"])

        #
        # Now delete the secret.
        self.discardMessages()
        h = lsa_conn.OpenSecret(
            handle=lsa_handle,
            name=secret_name,
            access_mask=security.SEC_FLAG_MAXIMUM_ALLOWED)

        lsa_conn.DeleteObject(h)
        messages = self.waitForMessages(1, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")

        dn = "cn=Test Secret,CN=System," + self.base_dn
        audit = messages[0]["dsdbChange"]
        self.assertEquals("Delete", audit["operation"])
        self.assertTrue(audit["performedAsSystem"])
        self.assertTrue(dn.lower(), audit["dn"].lower())
        self.assertRegexpMatches(audit["remoteAddress"],
                                 self.remoteAddress)
        self.assertTrue(self.is_guid(audit["sessionId"]))
        session_id = self.get_session()
        self.assertEquals(session_id, audit["sessionId"])
        service_description = self.get_service_description()
        self.assertEquals(service_description, "DCE/RPC")

    def test_modify(self):

        dn = "cn=" + USER_NAME + ",cn=users," + self.base_dn
        self.discardSetupMessages(dn)

        #
        # Add an attribute value
        #
        self.ldb.modify_ldif(
            "dn: " + dn + "\n" +
            "changetype: modify\n" +
            "add: carLicense\n" +
            "carLicense: license-01\n")

        messages = self.waitForMessages(1, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")

        audit = messages[0]["dsdbChange"]
        self.assertEquals("Modify", audit["operation"])
        self.assertFalse(audit["performedAsSystem"])
        self.assertEquals(dn, audit["dn"])
        self.assertRegexpMatches(audit["remoteAddress"],
                                 self.remoteAddress)
        self.assertTrue(self.is_guid(audit["sessionId"]))
        session_id = self.get_session()
        self.assertEquals(session_id, audit["sessionId"])
        service_description = self.get_service_description()
        self.assertEquals(service_description, "LDAP")

        attributes = audit["attributes"]
        self.assertEquals(1, len(attributes))
        actions = attributes["carLicense"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertEquals("add", actions[0]["action"])
        values = actions[0]["values"]
        self.assertEquals(1, len(values))
        self.assertEquals("license-01", values[0]["value"])

        #
        # Add an another value to the attribute
        #
        self.discardMessages()
        self.ldb.modify_ldif(
            "dn: " + dn + "\n" +
            "changetype: modify\n" +
            "add: carLicense\n" +
            "carLicense: license-02\n")

        messages = self.waitForMessages(1, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")
        attributes = messages[0]["dsdbChange"]["attributes"]
        self.assertEquals(1, len(attributes))
        actions = attributes["carLicense"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertEquals("add", actions[0]["action"])
        values = actions[0]["values"]
        self.assertEquals(1, len(values))
        self.assertEquals("license-02", values[0]["value"])

        #
        # Add an another two values to the attribute
        #
        self.discardMessages()
        self.ldb.modify_ldif(
            "dn: " + dn + "\n" +
            "changetype: modify\n" +
            "add: carLicense\n" +
            "carLicense: license-03\n" +
            "carLicense: license-04\n")

        messages = self.waitForMessages(1, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")
        attributes = messages[0]["dsdbChange"]["attributes"]
        self.assertEquals(1, len(attributes))
        actions = attributes["carLicense"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertEquals("add", actions[0]["action"])
        values = actions[0]["values"]
        self.assertEquals(2, len(values))
        self.assertEquals("license-03", values[0]["value"])
        self.assertEquals("license-04", values[1]["value"])

        #
        # delete two values to the attribute
        #
        self.discardMessages()
        self.ldb.modify_ldif(
            "dn: " + dn + "\n" +
            "changetype: delete\n" +
            "delete: carLicense\n" +
            "carLicense: license-03\n" +
            "carLicense: license-04\n")

        messages = self.waitForMessages(1, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")
        attributes = messages[0]["dsdbChange"]["attributes"]
        self.assertEquals(1, len(attributes))
        actions = attributes["carLicense"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertEquals("delete", actions[0]["action"])
        values = actions[0]["values"]
        self.assertEquals(2, len(values))
        self.assertEquals("license-03", values[0]["value"])
        self.assertEquals("license-04", values[1]["value"])

        #
        # replace two values to the attribute
        #
        self.discardMessages()
        self.ldb.modify_ldif(
            "dn: " + dn + "\n" +
            "changetype: delete\n" +
            "replace: carLicense\n" +
            "carLicense: license-05\n" +
            "carLicense: license-06\n")

        messages = self.waitForMessages(1, dn=dn)
        print("Received %d messages" % len(messages))
        self.assertEquals(1,
                          len(messages),
                          "Did not receive the expected number of messages")
        attributes = messages[0]["dsdbChange"]["attributes"]
        self.assertEquals(1, len(attributes))
        actions = attributes["carLicense"]["actions"]
        self.assertEquals(1, len(actions))
        self.assertEquals("replace", actions[0]["action"])
        values = actions[0]["values"]
        self.assertEquals(2, len(values))
        self.assertEquals("license-05", values[0]["value"])
        self.assertEquals("license-06", values[1]["value"])
