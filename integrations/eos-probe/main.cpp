// SPDX-License-Identifier: MIT
// Original standalone probe code. No Trainer game code or native structs.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include "private_io.h"
#include "measurements.h"
#include "peer_state.h"
#include <bcrypt.h>
#include <eos_sdk.h>
#include <eos_connect.h>
#include <eos_lobby.h>
#include <eos_p2p.h>
#include <eos_logging.h>
#include <atomic>
#include <cstring>
#include <cwchar>
#include <memory>
#include <thread>
#include <utility>

namespace eos_probe {
constexpr size_t PayloadBytes = wire::Bytes;
static_assert(PayloadBytes <= EOS_P2P_MAX_PACKET_SIZE);
std::atomic<bool> interrupted{false};
BOOL WINAPI stop_handler(DWORD) { interrupted.store(true); return TRUE; }

void event(const char* name, int64_t result = 0) {
    // Every name is a source-code literal; no SDK/user strings reach stdout.
    std::cout << "{\"report_version\":2,\"probe_version\":2,\"event\":\"" << name
        << "\",\"result\":" << result << "}\n" << std::flush;
}

struct Arguments {
    std::filesystem::path config, output, peer;
    std::wstring mode = L"identity";
    unsigned seconds = 30;
    DWORD parent_pid = 0;
    explicit Arguments(int count, wchar_t** args) {
        std::map<std::wstring, std::wstring> options;
        require(count >= 5 && count % 2 == 1);
        for (int at = 1; at < count; at += 2) {
            require(std::wcslen(args[at + 1]) <= 32760);
            require(options.emplace(args[at], args[at + 1]).second);
        }
        for (const auto& option : options) {
            const auto& key = option.first;
            const auto& value = option.second;
            if (key == L"--config") config = value;
            else if (key == L"--ticket-out") output = value;
            else if (key == L"--peer-ticket") peer = value;
            else if (key == L"--mode") mode = value;
            else if (key == L"--seconds") seconds = number(value, 300);
            else if (key == L"--parent-pid") parent_pid = number(value, MAXDWORD);
            else require(false);
        }
        require(!config.empty() && !output.empty() && seconds > 0);
        require(mode == L"identity" || mode == L"host" || mode == L"guest" || mode == L"uninvited");
        require((mode != L"guest" && mode != L"uninvited") || !peer.empty());
    }
    static unsigned number(const std::wstring& value, unsigned maximum) {
        require(!value.empty() && value.size() <= 10);
        uint64_t result = 0;
        for (wchar_t ch : value) {
            require(ch >= L'0' && ch <= L'9');
            result = result * 10 + (ch - L'0');
            require(result <= maximum);
        }
        return static_cast<unsigned>(result);
    }
};

struct Peer {
    EOS_ProductUserId id = nullptr;
    bool relayed = false;
    PeerExperiment experiment;
};

class Probe {
    Arguments arguments;
    Configuration configuration;
    std::unique_ptr<Ticket> target;
    Measurements measurements;
    EOS_HPlatform platform_handle = nullptr;
    EOS_HConnect connect = nullptr;
    EOS_HLobby lobbies = nullptr;
    EOS_HP2P p2p = nullptr;
    EOS_ProductUserId local = nullptr;
    EOS_P2P_SocketId socket{};
    EOS_NotificationId request_notice = EOS_INVALID_NOTIFICATIONID;
    EOS_NotificationId route_notice = EOS_INVALID_NOTIFICATIONID;
    EOS_NotificationId member_notice = EOS_INVALID_NOTIFICATIONID;
    EOS_NotificationId interrupted_notice = EOS_INVALID_NOTIFICATIONID;
    EOS_NotificationId closed_notice = EOS_INVALID_NOTIFICATIONID;
    std::array<Peer, 3> peers{};
    std::array<PeerExperiment, 3> retired{};
    size_t retired_count = 0;
    wire::Nonce incarnation{};
    wire::Role local_role = wire::Role::Guest;
    std::string lobby;
    HANDLE parent = nullptr;
    bool initialized = false, closing = false, fatal_route = false;
    bool membership_ended = false, membership_dirty = false;
    uint64_t next_binding = 1, scheduled_intervals_skipped = 0, queue_send_skips = 0;
    uint64_t queue_samples = 0, nonmember_requests_refused = 0;
    bool queue_ready = true;
    bool complete = false;
    EOS_EResult operation_result = EOS_EResult::EOS_UnexpectedError;
    Clock::time_point deadline;
    Clock::time_point next_tick = Clock::now();
    uint64_t sent = 0, received = 0, dropped = 0, send_skipped = 0, relayed_events = 0;

    uint64_t now_ns() const {
        return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(
            Clock::now() - measurements.started).count());
    }

    bool alive() const {
        return !interrupted.load() && Clock::now() < deadline &&
            (!parent || WaitForSingleObject(parent, 0) == WAIT_TIMEOUT);
    }
    void tick() {
        const auto before = Clock::now();
        EOS_Platform_Tick(platform_handle);
        measurements.tick_sample(before);
        next_tick += std::chrono::microseconds(16667);
        if (next_tick < Clock::now()) next_tick = Clock::now();
        std::this_thread::sleep_until(next_tick);
    }
    bool wait() {
        const auto timeout = (std::min)(deadline, Clock::now() + std::chrono::seconds(20));
        while (!complete && alive() && Clock::now() < timeout) tick();
        if (!complete) { event("operation_timeout"); require(false); }
        return operation_result == EOS_EResult::EOS_Success;
    }
    void result(EOS_EResult value, const char* name) {
        if (!EOS_EResult_IsOperationComplete(value)) return;
        operation_result = value;
        complete = true;
        event(name, static_cast<int64_t>(value));
    }
    static void EOS_CALL device_done(const EOS_Connect_CreateDeviceIdCallbackInfo* info) {
        static_cast<Probe*>(info->ClientData)->result(info->ResultCode, "device_id");
    }
    static void EOS_CALL created_user(const EOS_Connect_CreateUserCallbackInfo* info) {
        auto& self = *static_cast<Probe*>(info->ClientData);
        if (info->ResultCode == EOS_EResult::EOS_Success) self.local = info->LocalUserId;
        self.result(info->ResultCode, "create_user");
    }
    static void EOS_CALL logged_in(const EOS_Connect_LoginCallbackInfo* info) {
        auto& self = *static_cast<Probe*>(info->ClientData);
        if (info->ResultCode == EOS_EResult::EOS_InvalidUser && info->ContinuanceToken) {
            EOS_Connect_CreateUserOptions options{};
            options.ApiVersion = EOS_CONNECT_CREATEUSER_API_LATEST;
            options.ContinuanceToken = info->ContinuanceToken;
            EOS_Connect_CreateUser(self.connect, &options, &self, created_user);
            return;
        }
        if (info->ResultCode == EOS_EResult::EOS_Success) self.local = info->LocalUserId;
        self.result(info->ResultCode, "login");
    }
    void accept_lobby(EOS_EResult value, EOS_LobbyId id, const char* name) noexcept {
        try {
            if (value == EOS_EResult::EOS_Success) {
                require(id && strnlen_s(id, 129) <= 128);
                lobby = id;
            }
            result(value, name);
        } catch (...) { complete = true; operation_result = EOS_EResult::EOS_UnexpectedError; }
    }
    static void EOS_CALL lobby_created(const EOS_Lobby_CreateLobbyCallbackInfo* info) {
        static_cast<Probe*>(info->ClientData)->accept_lobby(info->ResultCode, info->LobbyId, "lobby_created");
    }
    static void EOS_CALL lobby_joined(const EOS_Lobby_JoinLobbyCallbackInfo* info) {
        static_cast<Probe*>(info->ClientData)->accept_lobby(info->ResultCode, info->LobbyId, "lobby_joined");
    }
    static void EOS_CALL invite_sent(const EOS_Lobby_SendInviteCallbackInfo* info) {
        static_cast<Probe*>(info->ClientData)->result(info->ResultCode, "invite_sent");
    }
    static void EOS_CALL invites_queried(const EOS_Lobby_QueryInvitesCallbackInfo* info) {
        static_cast<Probe*>(info->ClientData)->result(info->ResultCode, "invites_queried");
    }
    static void EOS_CALL lobby_left(const EOS_Lobby_LeaveLobbyCallbackInfo* info) {
        static_cast<Probe*>(info->ClientData)->result(info->ResultCode, "lobby_left");
    }
    void revoke_membership() {
        membership_ended = true;
        std::array<EOS_ProductUserId, 3> previous{};
        for (size_t at = 0; at < peers.size(); ++at) if (peers[at].id) {
            previous[at] = peers[at].id;
            if (retired_count < retired.size()) retired[retired_count++] = peers[at].experiment;
        }
        peers = {};  // Retire authorization before any SDK close/callback.
        for (auto id : previous) if (id) close_peer(id);
    }
    static void EOS_CALL member_status(const EOS_Lobby_LobbyMemberStatusReceivedCallbackInfo* info) {
        auto& self = *static_cast<Probe*>(info->ClientData);
        if (self.closing || self.membership_ended || self.lobby.empty() ||
                !info->LobbyId || self.lobby != info->LobbyId) return;
        using Status = EOS_ELobbyMemberStatus;
        if (info->CurrentStatus == Status::EOS_LMS_JOINED || info->CurrentStatus == Status::EOS_LMS_PROMOTED) {
            self.membership_dirty = true;
            return;
        }
        if (info->CurrentStatus != Status::EOS_LMS_LEFT && info->CurrentStatus != Status::EOS_LMS_DISCONNECTED &&
                info->CurrentStatus != Status::EOS_LMS_KICKED && info->CurrentStatus != Status::EOS_LMS_CLOSED) return;
        if (info->TargetUserId != self.local && !self.find_peer(info->TargetUserId)) return;
        // The experiment supports exactly one membership epoch. A departure
        // stops it; a later same-PUID packet/SESSION cannot reauthorize a rejoin.
        self.revoke_membership();
        event("membership_departure_stops_probe", static_cast<int64_t>(info->CurrentStatus));
    }
    static void EOS_CALL requested(const EOS_P2P_OnIncomingConnectionRequestInfo* info) {
        auto& self = *static_cast<Probe*>(info->ClientData);
        if (self.closing || self.membership_ended || info->LocalUserId != self.local || !info->SocketId ||
                std::strncmp(info->SocketId->SocketName, self.socket.SocketName, 33) != 0) {
            ++self.dropped;
            return;
        }
        if (!self.find_peer(info->RemoteUserId)) {
            ++self.dropped; ++self.nonmember_requests_refused;
            self.close_peer(info->RemoteUserId);
            event("nonmember_connection_refused", self.nonmember_requests_refused);
            return;
        }
        self.accept(info->RemoteUserId);
    }
    static void EOS_CALL established(const EOS_P2P_OnPeerConnectionEstablishedInfo* info) {
        auto& self = *static_cast<Probe*>(info->ClientData);
        if (self.closing || self.membership_ended || info->LocalUserId != self.local || !info->SocketId ||
                std::strncmp(info->SocketId->SocketName, self.socket.SocketName, 33) != 0) return;
        auto peer = self.find_peer(info->RemoteUserId);
        if (!peer) { ++self.dropped; return; }
        peer->relayed = info->NetworkType == EOS_ENetworkConnectionType::EOS_NCT_RelayedConnection;
        if (!peer->relayed) {
            self.fatal_route = true;
            self.close_peer(info->RemoteUserId);
            event("non_relay_route_refused");
        } else { ++self.relayed_events; event("relayed_connection"); }
    }
    static void EOS_CALL connection_interrupted(const EOS_P2P_OnPeerConnectionInterruptedInfo* info) {
        auto& self = *static_cast<Probe*>(info->ClientData);
        if (info->LocalUserId != self.local || !info->SocketId ||
                std::strncmp(info->SocketId->SocketName, self.socket.SocketName, 33) != 0) return;
        if (auto peer = self.find_peer(info->RemoteUserId)) {
            peer->relayed = false;
            peer->experiment.session_confirmed = false;
            event("connection_interrupted");
        }
    }
    static void EOS_CALL connection_closed(const EOS_P2P_OnRemoteConnectionClosedInfo* info) {
        auto& self = *static_cast<Probe*>(info->ClientData);
        if (info->LocalUserId != self.local || !info->SocketId ||
                std::strncmp(info->SocketId->SocketName, self.socket.SocketName, 33) != 0) return;
        if (auto peer = self.find_peer(info->RemoteUserId)) {
            peer->relayed = false;
            peer->experiment.session_confirmed = false;
            event("connection_closed", static_cast<int64_t>(info->Reason));
        }
    }
    Peer* find_peer(EOS_ProductUserId id) {
        for (auto& peer : peers) if (peer.id && peer.id == id) return &peer;
        return nullptr;
    }
    void accept(EOS_ProductUserId peer) {
        EOS_P2P_AcceptConnectionOptions options{};
        options.ApiVersion = EOS_P2P_ACCEPTCONNECTION_API_LATEST;
        options.LocalUserId = local; options.RemoteUserId = peer; options.SocketId = &socket;
        const auto status = EOS_P2P_AcceptConnection(p2p, &options);
        if (status != EOS_EResult::EOS_Success) event("accept_failed", static_cast<int64_t>(status));
    }
    void close_peer(EOS_ProductUserId peer) {
        EOS_P2P_CloseConnectionOptions options{};
        options.ApiVersion = EOS_P2P_CLOSECONNECTION_API_LATEST;
        options.LocalUserId = local; options.RemoteUserId = peer; options.SocketId = &socket;
        EOS_P2P_CloseConnection(p2p, &options);
    }
    bool safe_details(EOS_HLobbyDetails details, EOS_ProductUserId* owner = nullptr) {
        EOS_LobbyDetails_CopyInfoOptions options{};
        options.ApiVersion = EOS_LOBBYDETAILS_COPYINFO_API_LATEST;
        EOS_LobbyDetails_Info* info = nullptr;
        const auto status = EOS_LobbyDetails_CopyInfo(details, &options, &info);
        if (status != EOS_EResult::EOS_Success || !info) return false;
        const bool safe = info->PermissionLevel == EOS_ELobbyPermissionLevel::EOS_LPL_INVITEONLY && info->MaxMembers == 4 &&
            !info->bPresenceEnabled && !info->bRTCRoomEnabled && !info->bAllowJoinById &&
            info->bRejoinAfterKickRequiresInvite &&
            (arguments.mode != L"host" || info->LobbyOwnerUserId == local) &&
            (!target || (arguments.mode != L"guest" && arguments.mode != L"uninvited") ||
             (target->lobby == info->LobbyId &&
              EOS_ProductUserId_FromString(target->puid.c_str()) == info->LobbyOwnerUserId));
        if (safe && owner) *owner = info->LobbyOwnerUserId;
        EOS_LobbyDetails_Info_Release(info);
        return safe;
    }
    bool refresh_members() {
        if (membership_ended) return false;
        EOS_Lobby_CopyLobbyDetailsHandleOptions options{};
        options.ApiVersion = EOS_LOBBY_COPYLOBBYDETAILSHANDLE_API_LATEST;
        options.LocalUserId = local; options.LobbyId = lobby.c_str();
        EOS_HLobbyDetails details = nullptr;
        if (EOS_Lobby_CopyLobbyDetailsHandle(lobbies, &options, &details) != EOS_EResult::EOS_Success) return false;
        EOS_LobbyDetails_GetMemberCountOptions count_options{};
        count_options.ApiVersion = EOS_LOBBYDETAILS_GETMEMBERCOUNT_API_LATEST;
        const uint32_t count = EOS_LobbyDetails_GetMemberCount(details, &count_options);
        EOS_ProductUserId owner = nullptr;
        const bool safe = safe_details(details, &owner) && count > 0 && count <= 4;
        std::array<Peer, 3> next{};
        size_t used = 0;
        bool local_present = false;
        if (safe) for (uint32_t at = 0; at < count; ++at) {
            EOS_LobbyDetails_GetMemberByIndexOptions member_options{};
            member_options.ApiVersion = EOS_LOBBYDETAILS_GETMEMBERBYINDEX_API_LATEST;
            member_options.MemberIndex = at;
            const auto id = EOS_LobbyDetails_GetMemberByIndex(details, &member_options);
            if (id == local) { local_present = true; continue; }
            if (!id || used >= next.size()) continue;
            const auto old = find_peer(id);
            if (old) next[used++] = *old;
            else {
                Peer fresh{};
                fresh.id = id;
                fresh.experiment.binding = next_binding++;
                fresh.experiment.role = id == owner ? wire::Role::Host : wire::Role::Guest;
                next[used++] = fresh;
            }
        }
        EOS_LobbyDetails_Release(details);
        if (!safe || !local_present) return false;
        local_role = local == owner ? wire::Role::Host : wire::Role::Guest;
        for (const auto& old : peers) if (old.id) {
            const bool retained = std::any_of(next.begin(), next.end(),
                [&](const Peer& candidate) { return candidate.id == old.id; });
            if (!retained) {
                // Reconciliation can discover removal before its notice. Keep
                // the old experiments for the final report, then revoke all.
                revoke_membership();
                event("membership_reconciliation_stops_probe");
                return false;
            }
        }
        peers = next;
        if (request_notice != EOS_INVALID_NOTIFICATIONID && route_notice != EOS_INVALID_NOTIFICATIONID)
            for (const auto& peer : peers) if (peer.id) accept(peer.id);
        event("member_count", count);
        membership_dirty = false;
        return true;
    }
    void export_ticket() {
        std::array<char, EOS_PRODUCTUSERID_MAX_LENGTH + 1> value{};
        int32_t size = static_cast<int32_t>(value.size());
        require(EOS_ProductUserId_ToString(local, value.data(), &size) == EOS_EResult::EOS_Success);
        std::string body = "version=2\npuid=" + std::string(value.data()) + "\n";
        if (!lobby.empty()) body += "lobby=" + lobby + "\nsocket=" + socket.SocketName + "\n";
        write_ticket(arguments.output, body);
        event("private_ticket_written");
    }
    void host() {
        std::array<unsigned char, 16> random{};
        require(BCryptGenRandom(nullptr, random.data(), static_cast<ULONG>(random.size()),
                              BCRYPT_USE_SYSTEM_PREFERRED_RNG) == 0);
        const char digits[] = "0123456789abcdef";
        for (size_t at = 0; at < random.size(); ++at) {
            socket.SocketName[2 * at] = digits[random[at] >> 4];
            socket.SocketName[2 * at + 1] = digits[random[at] & 15];
        }
        EOS_Lobby_CreateLobbyOptions options{};
        options.ApiVersion = EOS_LOBBY_CREATELOBBY_API_LATEST;
        options.LocalUserId = local; options.MaxLobbyMembers = 4;
        options.PermissionLevel = EOS_ELobbyPermissionLevel::EOS_LPL_INVITEONLY;
        options.bAllowInvites = EOS_TRUE; options.BucketId = "private-eos-probe-v2";
        options.bDisableHostMigration = EOS_TRUE;
        options.bRejoinAfterKickRequiresInvite = EOS_TRUE;
        complete = false;
        EOS_Lobby_CreateLobby(lobbies, &options, this, lobby_created);
        require(wait() && !lobby.empty() && refresh_members());
        export_ticket();
        event("host_invitation_enabled", target ? 1 : 0);
        if (target) {
            EOS_Lobby_SendInviteOptions invite{};
            invite.ApiVersion = EOS_LOBBY_SENDINVITE_API_LATEST;
            invite.LocalUserId = local; invite.LobbyId = lobby.c_str();
            invite.TargetUserId = EOS_ProductUserId_FromString(target->puid.c_str());
            require(invite.TargetUserId && invite.TargetUserId != local);
            complete = false;
            EOS_Lobby_SendInvite(lobbies, &invite, this, invite_sent);
            require(wait());
        }
    }
    bool try_invitation() {
        EOS_Lobby_QueryInvitesOptions query{};
        query.ApiVersion = EOS_LOBBY_QUERYINVITES_API_LATEST; query.LocalUserId = local;
        complete = false;
        EOS_Lobby_QueryInvites(lobbies, &query, this, invites_queried);
        if (!wait()) return false;
        EOS_Lobby_GetInviteCountOptions count_options{};
        count_options.ApiVersion = EOS_LOBBY_GETINVITECOUNT_API_LATEST;
        count_options.LocalUserId = local;
        const auto count = EOS_Lobby_GetInviteCount(lobbies, &count_options);
        event("invite_count", count);
        for (uint32_t at = 0; at < (std::min)(count, 32u); ++at) {
            std::array<char, 512> invite{};
            int32_t capacity = static_cast<int32_t>(invite.size());
            EOS_Lobby_GetInviteIdByIndexOptions index{};
            index.ApiVersion = EOS_LOBBY_GETINVITEIDBYINDEX_API_LATEST;
            index.LocalUserId = local; index.Index = at;
            if (EOS_Lobby_GetInviteIdByIndex(lobbies, &index, invite.data(), &capacity) != EOS_EResult::EOS_Success) continue;
            EOS_Lobby_CopyLobbyDetailsHandleByInviteIdOptions copy{};
            copy.ApiVersion = EOS_LOBBY_COPYLOBBYDETAILSHANDLEBYINVITEID_API_LATEST;
            copy.InviteId = invite.data();
            EOS_HLobbyDetails details = nullptr;
            if (EOS_Lobby_CopyLobbyDetailsHandleByInviteId(lobbies, &copy, &details) != EOS_EResult::EOS_Success) continue;
            if (!safe_details(details)) { EOS_LobbyDetails_Release(details); continue; }
            EOS_Lobby_JoinLobbyOptions join{};
            join.ApiVersion = EOS_LOBBY_JOINLOBBY_API_LATEST;
            join.LocalUserId = local; join.LobbyDetailsHandle = details;
            complete = false;
            EOS_Lobby_JoinLobby(lobbies, &join, this, lobby_joined);
            const bool joined = wait();
            EOS_LobbyDetails_Release(details);
            return joined;
        }
        return false;
    }
    void guest() {
        require(target && !target->lobby.empty());
        std::memcpy(socket.SocketName, target->socket.c_str(), 33);
        auto next = Clock::now();
        while (alive() && lobby.empty()) {
            if (Clock::now() >= next) {
                if (try_invitation()) break;
                next = Clock::now() + std::chrono::seconds(2);
            }
            tick();
        }
        require(!lobby.empty() && refresh_members());
        export_ticket();
    }
    int uninvited() {
        require(target && !target->lobby.empty());
        const auto remote = EOS_ProductUserId_FromString(target->puid.c_str());
        require(remote && remote != local);
        std::memcpy(socket.SocketName, target->socket.c_str(), 33);
        export_ticket();  // Actual authenticated identity; no lobby is joined.
        EOS_P2P_AcceptConnectionOptions accept_options{};
        accept_options.ApiVersion = EOS_P2P_ACCEPTCONNECTION_API_LATEST;
        accept_options.LocalUserId = local; accept_options.RemoteUserId = remote;
        accept_options.SocketId = &socket;
        const auto accepted = EOS_P2P_AcceptConnection(p2p, &accept_options);
        event("uninvited_local_accept_status", static_cast<int64_t>(accepted));
        require(accepted == EOS_EResult::EOS_Success);
        uint64_t attempts = 0, queued = 0, next_ns = 0;
        wire::Packet session{};
        session.kind = wire::Kind::Session; session.role = wire::Role::Guest; session.sender = incarnation;
        const auto bytes = wire::encode(session);
        while (alive()) {
            tick(); sample_queues();
            const auto timestamp = now_ns();
            if (timestamp < next_ns) continue;
            next_ns = timestamp + wire::EchoPeriodNs;
            if (!queue_ready) { ++queue_send_skips; continue; }
            // Deliberately bypass the *local* member guard in this explicit
            // hostile-client experiment. The unchanged host must refuse it.
            EOS_P2P_SendPacketOptions send{};
            send.ApiVersion = EOS_P2P_SENDPACKET_API_LATEST;
            send.LocalUserId = local; send.RemoteUserId = remote; send.SocketId = &socket;
            send.Channel = wire::SessionChannel; send.Data = bytes.data();
            send.DataLengthBytes = static_cast<uint32_t>(bytes.size());
            send.bAllowDelayedDelivery = EOS_FALSE;
            send.Reliability = EOS_EPacketReliability::EOS_PR_UnreliableUnordered;
            send.bDisableAutoAcceptConnection = EOS_TRUE;
            ++attempts;
            EOS_P2P_AcceptConnection(p2p, &accept_options);  // Explicit bounded hostile retry after a refusal.
            if (EOS_P2P_SendPacket(p2p, &send) == EOS_EResult::EOS_Success) ++queued;
        }
        event("uninvited_sdk_attempts", attempts); event("uninvited_sdk_queued", queued);
        event("uninvited_host_outcome_known_locally", 0);
        event("server_join_rejection_exercised", 0);
        return 0;  // Observation complete, not proof that the host rejected us.
    }
    bool send_packet(Peer& peer, const wire::Packet& packet, uint8_t channel) {
        if (!queue_ready || closing || membership_ended || !peer.id) {
            ++peer.experiment.send_skipped; ++queue_send_skips; return false;
        }
        const auto bytes = wire::encode(packet);
        EOS_P2P_SendPacketOptions options{};
        options.ApiVersion = EOS_P2P_SENDPACKET_API_LATEST;
        options.LocalUserId = local; options.RemoteUserId = peer.id; options.SocketId = &socket;
        options.Channel = channel; options.DataLengthBytes = static_cast<uint32_t>(bytes.size());
        options.Data = bytes.data(); options.bAllowDelayedDelivery = EOS_FALSE;
        options.Reliability = EOS_EPacketReliability::EOS_PR_UnreliableUnordered;
        options.bDisableAutoAcceptConnection = EOS_TRUE;
        if (EOS_P2P_SendPacket(p2p, &options) == EOS_EResult::EOS_Success) { ++sent; return true; }
        ++send_skipped; ++peer.experiment.send_skipped;
        return false;
    }
    void receive_data(Peer& peer, const wire::Packet& packet, uint64_t arrival_ns) {
        auto& experiment = peer.experiment;
        if (packet.kind == wire::Kind::Session) {
            const bool first = !experiment.session_bound;
            if (!experiment.bind_session(packet)) { ++dropped; return; }
            if (first) event("session_bound", experiment.binding);
            return;
        }
        if (!experiment.matches(packet, incarnation)) { ++dropped; return; }
        experiment.session_confirmed = true;
        if (packet.kind == wire::Kind::Echo) {
            experiment.round_trips.echo(packet.echoed_sequence, packet.timestamp_ns, arrival_ns);
            return;  // Never echo an echo.
        }
        ++received;
        const bool fresh_sequence = experiment.arrivals.observe(packet.sequence, arrival_ns);
        if (!fresh_sequence || !experiment.echo_due(packet.sequence, arrival_ns)) return;
        wire::Packet reply{};
        reply.kind = wire::Kind::Echo; reply.role = local_role;
        reply.sender = incarnation; reply.recipient = packet.sender;
        reply.sequence = experiment.next_echo_sequence;
        reply.echoed_sequence = packet.sequence; reply.timestamp_ns = packet.timestamp_ns;
        if (send_packet(peer, reply, wire::DataChannel)) {
            ++experiment.next_echo_sequence; ++experiment.echoes_sent;
        }
    }
    void receive() {
        for (unsigned at = 0; at < 64; ++at) {
            uint32_t original_size = 0;
            EOS_P2P_GetNextReceivedPacketSizeOptions next{};
            next.ApiVersion = EOS_P2P_GETNEXTRECEIVEDPACKETSIZE_API_LATEST;
            next.LocalUserId = local; next.RequestedChannel = nullptr;
            if (EOS_P2P_GetNextReceivedPacketSize(p2p, &next, &original_size) != EOS_EResult::EOS_Success) return;
            std::array<unsigned char, EOS_P2P_MAX_PACKET_SIZE> bytes{};
            EOS_P2P_ReceivePacketOptions options{};
            options.ApiVersion = EOS_P2P_RECEIVEPACKET_API_LATEST;
            options.LocalUserId = local; options.RequestedChannel = nullptr;
            options.MaxDataSizeBytes = static_cast<uint32_t>(bytes.size());
            EOS_ProductUserId sender = nullptr;
            EOS_P2P_SocketId incoming{};
            uint8_t incoming_channel = 0;
            uint32_t copied = 0;
            const auto status = EOS_P2P_ReceivePacket(p2p, &options, &sender, &incoming,
                &incoming_channel, bytes.data(), &copied);
            if (status != EOS_EResult::EOS_Success) { ++dropped; return; }
            auto peer = find_peer(sender);
            wire::Packet packet{};
            if (original_size != PayloadBytes || copied != original_size || !peer || !peer->relayed ||
                    std::strncmp(incoming.SocketName, socket.SocketName, 33) != 0 ||
                    !wire::decode(bytes.data(), copied, incoming_channel, packet)) { ++dropped; continue; }
            receive_data(*peer, packet, now_ns());
        }
    }
    void sample_queues() {
        EOS_P2P_GetPacketQueueInfoOptions options{};
        options.ApiVersion = EOS_P2P_GETPACKETQUEUEINFO_API_LATEST;
        EOS_P2P_PacketQueueInfo queues{};
        require(EOS_P2P_GetPacketQueueInfo(p2p, &options, &queues) == EOS_EResult::EOS_Success);
        measurements.incoming_packets_max = (std::max)(measurements.incoming_packets_max,
            queues.IncomingPacketQueueCurrentPacketCount);
        measurements.outgoing_packets_max = (std::max)(measurements.outgoing_packets_max,
            queues.OutgoingPacketQueueCurrentPacketCount);
        measurements.incoming_bytes_max = (std::max)(measurements.incoming_bytes_max,
            queues.IncomingPacketQueueCurrentSizeBytes);
        measurements.outgoing_bytes_max = (std::max)(measurements.outgoing_bytes_max,
            queues.OutgoingPacketQueueCurrentSizeBytes);
        queue_ready = queues.OutgoingPacketQueueCurrentPacketCount <= 9 &&
            queues.OutgoingPacketQueueCurrentSizeBytes <= 16384;
        ++queue_samples;
    }
    void sessions(uint64_t timestamp_ns) {
        for (auto& peer : peers) if (peer.id && !peer.experiment.session_confirmed &&
                timestamp_ns >= peer.experiment.next_session_ns) {
            // One bounded, idempotent bootstrap/SESSION attempt per333ms. It
            // starts accepted connections without authorizing any DATA packet.
            peer.experiment.next_session_ns = timestamp_ns + wire::EchoPeriodNs;
            wire::Packet packet{};
            packet.kind = wire::Kind::Session; packet.role = local_role; packet.sender = incarnation;
            if (send_packet(peer, packet, wire::SessionChannel)) ++peer.experiment.sessions_sent;
        }
    }
    void workload(uint64_t timestamp_ns) {
        for (auto& peer : peers) if (peer.id && peer.relayed && peer.experiment.session_bound) {
            auto& experiment = peer.experiment;
            wire::Packet packet{};
            packet.kind = wire::Kind::Data; packet.role = local_role;
            packet.sender = incarnation; packet.recipient = experiment.remote_incarnation;
            packet.sequence = experiment.next_sequence; packet.timestamp_ns = timestamp_ns;
            if (packet.sequence > wire::MaximumSequence) { ++send_skipped; continue; }
            if (send_packet(peer, packet, wire::DataChannel)) {
                experiment.round_trips.expire(timestamp_ns);
                experiment.round_trips.sent(packet.sequence, timestamp_ns);
                ++experiment.next_sequence; ++experiment.data_sent;
            }
        }
    }
    void initialize() {
        EOS_InitializeOptions init{};
        init.ApiVersion = EOS_INITIALIZE_API_LATEST;
        init.ProductName = "SM64 Trainer Standalone EOS Probe"; init.ProductVersion = "0.0.2";
        require(EOS_Initialize(&init) == EOS_EResult::EOS_Success);
        initialized = true;
        require(EOS_Logging_SetLogLevel(EOS_ELogCategory::EOS_LC_ALL_CATEGORIES,
            EOS_ELogLevel::EOS_LOG_Off) == EOS_EResult::EOS_Success);
        EOS_Platform_Options options{};
        options.ApiVersion = EOS_PLATFORM_OPTIONS_API_LATEST;
        options.ProductId = configuration.get("product_id");
        options.SandboxId = configuration.get("sandbox_id");
        options.DeploymentId = configuration.get("deployment_id");
        options.ClientCredentials.ClientId = configuration.get("client_id");
        options.ClientCredentials.ClientSecret = configuration.get("client_secret");
        options.Flags = EOS_PF_DISABLE_OVERLAY | EOS_PF_DISABLE_SOCIAL_OVERLAY;
        options.TickBudgetInMilliseconds = 2;
        platform_handle = EOS_Platform_Create(&options);
        require(platform_handle != nullptr);
        connect = EOS_Platform_GetConnectInterface(platform_handle);
        lobbies = EOS_Platform_GetLobbyInterface(platform_handle);
        p2p = EOS_Platform_GetP2PInterface(platform_handle);
        require(connect && lobbies && p2p);
        EOS_P2P_SetRelayControlOptions relay{};
        relay.ApiVersion = EOS_P2P_SETRELAYCONTROL_API_LATEST;
        relay.RelayControl = EOS_ERelayControl::EOS_RC_ForceRelays;
        require(EOS_P2P_SetRelayControl(p2p, &relay) == EOS_EResult::EOS_Success);
        EOS_P2P_GetRelayControlOptions get{};
        get.ApiVersion = EOS_P2P_GETRELAYCONTROL_API_LATEST;
        EOS_ERelayControl value = EOS_ERelayControl::EOS_RC_AllowRelays;
        require(EOS_P2P_GetRelayControl(p2p, &get, &value) == EOS_EResult::EOS_Success &&
            value == EOS_ERelayControl::EOS_RC_ForceRelays);
        event("force_relays_readback", static_cast<int64_t>(value));
        EOS_P2P_SetPacketQueueSizeOptions queues{};
        queues.ApiVersion = EOS_P2P_SETPACKETQUEUESIZE_API_LATEST;
        queues.IncomingPacketQueueMaxSizeBytes = 65536; queues.OutgoingPacketQueueMaxSizeBytes = 65536;
        require(EOS_P2P_SetPacketQueueSize(p2p, &queues) == EOS_EResult::EOS_Success);
        socket.ApiVersion = EOS_P2P_SOCKETID_API_LATEST;
    }
    void login() {
        EOS_Connect_CreateDeviceIdOptions device{};
        device.ApiVersion = EOS_CONNECT_CREATEDEVICEID_API_LATEST; device.DeviceModel = "Windows PC";
        complete = false;
        EOS_Connect_CreateDeviceId(connect, &device, this, device_done);
        wait();  // A timeout throws before another operation can reuse the wait.
        require(operation_result == EOS_EResult::EOS_Success ||
            operation_result == EOS_EResult::EOS_DuplicateNotAllowed);
        EOS_Connect_Credentials credentials{};
        credentials.ApiVersion = EOS_CONNECT_CREDENTIALS_API_LATEST;
        credentials.Type = EOS_EExternalCredentialType::EOS_ECT_DEVICEID_ACCESS_TOKEN;
        EOS_Connect_UserLoginInfo user{};
        user.ApiVersion = EOS_CONNECT_USERLOGININFO_API_LATEST; user.DisplayName = "SM64Probe";
        EOS_Connect_LoginOptions options{};
        options.ApiVersion = EOS_CONNECT_LOGIN_API_LATEST;
        options.Credentials = &credentials; options.UserLoginInfo = &user;
        complete = false;
        EOS_Connect_Login(connect, &options, this, logged_in);
        require(wait() && local && EOS_ProductUserId_IsValid(local));
    }
    void notifications() {
        EOS_P2P_AddNotifyPeerConnectionRequestOptions request{};
        request.ApiVersion = EOS_P2P_ADDNOTIFYPEERCONNECTIONREQUEST_API_LATEST;
        request.LocalUserId = local; request.SocketId = &socket;
        request_notice = EOS_P2P_AddNotifyPeerConnectionRequest(p2p, &request, this, requested);
        EOS_P2P_AddNotifyPeerConnectionEstablishedOptions route{};
        route.ApiVersion = EOS_P2P_ADDNOTIFYPEERCONNECTIONESTABLISHED_API_LATEST;
        route.LocalUserId = local; route.SocketId = &socket;
        route_notice = EOS_P2P_AddNotifyPeerConnectionEstablished(p2p, &route, this, established);
        EOS_P2P_AddNotifyPeerConnectionInterruptedOptions interrupt{};
        interrupt.ApiVersion = EOS_P2P_ADDNOTIFYPEERCONNECTIONINTERRUPTED_API_LATEST;
        interrupt.LocalUserId = local; interrupt.SocketId = &socket;
        interrupted_notice = EOS_P2P_AddNotifyPeerConnectionInterrupted(p2p, &interrupt, this, connection_interrupted);
        EOS_P2P_AddNotifyPeerConnectionClosedOptions close{};
        close.ApiVersion = EOS_P2P_ADDNOTIFYPEERCONNECTIONCLOSED_API_LATEST;
        close.LocalUserId = local; close.SocketId = &socket;
        closed_notice = EOS_P2P_AddNotifyPeerConnectionClosed(p2p, &close, this, connection_closed);
        require(request_notice != EOS_INVALID_NOTIFICATIONID && route_notice != EOS_INVALID_NOTIFICATIONID &&
            interrupted_notice != EOS_INVALID_NOTIFICATIONID && closed_notice != EOS_INVALID_NOTIFICATIONID);
    }
    void lobby_notifications() {
        EOS_Lobby_AddNotifyLobbyMemberStatusReceivedOptions options{};
        options.ApiVersion = EOS_LOBBY_ADDNOTIFYLOBBYMEMBERSTATUSRECEIVED_API_LATEST;
        member_notice = EOS_Lobby_AddNotifyLobbyMemberStatusReceived(lobbies, &options, this, member_status);
        require(member_notice != EOS_INVALID_NOTIFICATIONID);
    }
    void report_peer(PeerExperiment& experiment, uint64_t timestamp) {
        experiment.round_trips.expire(timestamp);
        const auto& sequences = experiment.arrivals;
        const auto& rtt = experiment.round_trips;
        const double last_age_ms = sequences.last_arrival_ns && timestamp >= sequences.last_arrival_ns ?
            static_cast<double>(timestamp - sequences.last_arrival_ns) / 1000000.0 : -1;
        std::cout << "{\"report_version\":2,\"probe_version\":2,\"event\":\"peer_experiment\",\"binding\":"
            << experiment.binding << ",\"role\":" << static_cast<unsigned>(experiment.role)
            << ",\"session_bound\":" << experiment.session_bound
            << ",\"session_confirmed\":" << experiment.session_confirmed
            << ",\"sessions_queued\":" << experiment.sessions_sent
            << ",\"sessions_received\":" << experiment.sessions_received
            << ",\"session_mismatch\":" << experiment.session_mismatch
            << ",\"packet_mismatch\":" << experiment.packet_mismatch
            << ",\"data_queued\":" << experiment.data_sent
            << ",\"echoes_queued\":" << experiment.echoes_sent
            << ",\"echoes_rate_limited\":" << experiment.echoes_suppressed
            << ",\"send_skipped\":" << experiment.send_skipped
            << ",\"data_unique\":" << sequences.unique << ",\"data_duplicate\":" << sequences.duplicate
            << ",\"data_reordered\":" << sequences.reordered
            << ",\"older_than_window\":" << sequences.older_than_window
            << ",\"first_received_sequence\":" << sequences.first
            << ",\"highest_received_sequence\":" << sequences.high
            << ",\"initial_prefix_unobserved\":" << (sequences.first ? sequences.first - 1 : 0)
            << ",\"gap_events\":" << sequences.gap_events << ",\"gap_positions_observed\":" << sequences.gap_positions
            << ",\"window_holes_at_end\":" << sequences.holes()
            << ",\"aged_out_unresolved\":" << sequences.aged_unresolved
            << ",\"ending_tail_unknown\":true,\"network_loss_known\":false,\"one_way_delay_known\":false"
            << ",\"receiver_local_last_arrival_age_ms\":" << last_age_ms
            << ",\"receiver_local_max_arrival_gap_ms\":" << static_cast<double>(sequences.maximum_arrival_gap_ns) / 1000000.0
            << ",\"rtt_samples\":" << rtt.count << ",\"rtt_p50_us\":" << rtt.percentile(.50)
            << ",\"rtt_p95_us\":" << rtt.percentile(.95) << ",\"rtt_p99_us\":" << rtt.percentile(.99)
            << ",\"rtt_max_us\":" << rtt.percentile(1)
            << ",\"rtt_invalid\":" << rtt.invalid << ",\"rtt_duplicate\":" << rtt.duplicate
            << ",\"rtt_expired\":" << rtt.expired << ",\"rtt_unresolved_overwritten\":" << rtt.unresolved_overwritten
            << ",\"rtt_unresolved_at_end\":" << rtt.pending()
            << ",\"rtt_oldest_pending_ms\":" << rtt.oldest_pending_ms(timestamp)
            << ",\"rtt_sample_overflow\":" << rtt.overflow << "}\n";
    }
public:
    explicit Probe(Arguments args) : arguments(std::move(args)), configuration(arguments.config),
            deadline(Clock::now() + std::chrono::seconds(arguments.seconds)) {
        if (!arguments.peer.empty()) target = std::make_unique<Ticket>(arguments.peer);
        require(BCryptGenRandom(nullptr, incarnation.data(), static_cast<ULONG>(incarnation.size()),
                              BCRYPT_USE_SYSTEM_PREFERRED_RNG) == 0 && wire::nonzero(incarnation));
        if (arguments.parent_pid) {
            parent = OpenProcess(SYNCHRONIZE, FALSE, arguments.parent_pid);
            require(parent != nullptr);
        }
    }
    int run() {
        event("probe_version", wire::Version);
        event("report_version", 2);
        event("payload_bytes", wire::Bytes);
        event("requested_data_hz", 30);
        event("maximum_echo_hz_per_peer", 3);
        event("single_membership_epoch", 1);
        initialize(); login();
        if (arguments.mode == L"uninvited") return uninvited();
        lobby_notifications();
        if (arguments.mode == L"identity") export_ticket();
        else if (arguments.mode == L"host") host();
        else guest();
        if (!lobby.empty()) notifications();
        auto next_members = Clock::now();
        uint64_t next_send_ns = now_ns();
        while (alive() && !fatal_route && !membership_ended) {
            tick();
            if (membership_ended || fatal_route) break;
            const auto now = Clock::now();
            if (now >= next_members || membership_dirty) {
                if (!lobby.empty() && !refresh_members()) {
                    if (membership_ended) break;
                    require(false);
                }
                measurements.process_sample();
                next_members = now + std::chrono::seconds(1);
            }
            sample_queues();
            const uint64_t timestamp = now_ns();
            for (auto& peer : peers) if (peer.id) peer.experiment.round_trips.expire(timestamp);
            if (!lobby.empty()) {
                sessions(timestamp);
                receive();
                const auto intervals = wire::advance_sample(timestamp, next_send_ns);
                if (intervals) {
                    scheduled_intervals_skipped += intervals - 1;
                    workload(now_ns());
                }
            }
        }
        event("probe_packets_queued", sent); event("probe_data_received_valid", received);
        event("probe_packets_rejected", dropped); event("probe_sends_skipped", send_skipped);
        event("scheduled_intervals_skipped", scheduled_intervals_skipped);
        event("queue_send_skips", queue_send_skips); event("queue_samples", queue_samples);
        event("nonmember_requests_refused", nonmember_requests_refused);
        event("relayed_route_events", relayed_events);
        return fatal_route ? 4 : 0;
    }
    ~Probe() {
        const uint64_t timestamp = now_ns();
        for (auto& peer : peers) if (peer.id) report_peer(peer.experiment, timestamp);
        for (size_t at = 0; at < retired_count; ++at) report_peer(retired[at], timestamp);
        closing = true; peers = {};
        if (platform_handle) {
            if (local) {
                EOS_P2P_CloseConnectionsOptions close{};
                close.ApiVersion = EOS_P2P_CLOSECONNECTIONS_API_LATEST;
                close.LocalUserId = local; close.SocketId = &socket;
                EOS_P2P_CloseConnections(p2p, &close);
            }
            if (!lobby.empty()) {
                EOS_Lobby_LeaveLobbyOptions leave{};
                leave.ApiVersion = EOS_LOBBY_LEAVELOBBY_API_LATEST;
                leave.LocalUserId = local; leave.LobbyId = lobby.c_str();
                complete = false;
                EOS_Lobby_LeaveLobby(lobbies, &leave, this, lobby_left);
                const auto end = Clock::now() + std::chrono::seconds(3);
                while (!complete && Clock::now() < end) tick();
            }
            if (request_notice != EOS_INVALID_NOTIFICATIONID)
                EOS_P2P_RemoveNotifyPeerConnectionRequest(p2p, request_notice);
            if (route_notice != EOS_INVALID_NOTIFICATIONID)
                EOS_P2P_RemoveNotifyPeerConnectionEstablished(p2p, route_notice);
            if (interrupted_notice != EOS_INVALID_NOTIFICATIONID)
                EOS_P2P_RemoveNotifyPeerConnectionInterrupted(p2p, interrupted_notice);
            if (closed_notice != EOS_INVALID_NOTIFICATIONID)
                EOS_P2P_RemoveNotifyPeerConnectionClosed(p2p, closed_notice);
            if (member_notice != EOS_INVALID_NOTIFICATIONID)
                EOS_Lobby_RemoveNotifyLobbyMemberStatusReceived(lobbies, member_notice);
            const auto before = Clock::now();
            EOS_Platform_Release(platform_handle);
            event("platform_release_us", static_cast<int64_t>(
                std::chrono::duration<double, std::micro>(Clock::now() - before).count()));
        }
        if (initialized) event("sdk_shutdown", static_cast<int64_t>(EOS_Shutdown()));
        if (parent) CloseHandle(parent);
        measurements.report();
    }
};
}  // namespace eos_probe

int wmain(int count, wchar_t** args) {
    if (count == 2 && std::wcscmp(args[1], L"--help") == 0) {
        std::cout << "eos-probe --config FILE --ticket-out NEW_PRIVATE_FILE "
            "[--mode identity|host|guest|uninvited] [--peer-ticket PRIVATE_FILE] "
            "[--seconds 1..300] [--parent-pid PID]\n";
        return 0;
    }
    SetConsoleCtrlHandler(eos_probe::stop_handler, TRUE);
    try {
        eos_probe::Probe probe{eos_probe::Arguments(count, args)};
        return probe.run();
    } catch (...) {
        eos_probe::event("probe_failed");  // Never print exceptions or SDK log bodies.
        return 2;
    }
}
