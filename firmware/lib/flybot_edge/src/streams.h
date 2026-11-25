// Contract section 6 rules 3-8 and 13: per-stream sequence tracking (port of
// contract/flybot_contract/streams.py StreamTracker). Fixed memory: at most kMaxStreams streams;
// when full, the least recently seen stream is forgotten early (rule 13 would forget it after
// 10 minutes anyway; only many reloaded dashboard tabs within 10 minutes can fill it).
#pragma once
#include <cstdint>
#include "opt.h"

namespace flybot {

enum class Topic : uint8_t { CmdMotor, CmdManual, CmdMode, CmdSleep, Pong, Other };

struct StreamKey {
    Topic topic;
    char src[16];  // sender name on topics with src, else ""
    static StreamKey of(Topic t, const char* src = "");
    bool operator==(const StreamKey& o) const;
};

class StreamTracker {
public:
    static constexpr int kMaxStreams = 16;
    explicit StreamTracker(int64_t idle_forget_ms = 600000) : idle_forget_ms_(idle_forget_ms) {}

    // True if the message is new and in order. Duplicates and late messages are dropped.
    bool accept(const StreamKey& key, uint32_t seq, int64_t up, int64_t now,
                Opt<uint32_t> bid = none);
    // Set a stream's state without checks (rule 10: a retained message is current state).
    void remember(const StreamKey& key, uint32_t seq, int64_t up, int64_t now,
                  Opt<uint32_t> bid = none);
    void reset(const StreamKey& key);

    uint64_t lost = 0, restarts = 0, dropped = 0, evicted = 0;
    bool last_restarted = false;
    int size() const;

private:
    struct Entry {
        bool used = false;
        StreamKey key{};
        uint32_t seq = 0;
        int64_t up = 0, last_seen = 0;
        bool has_bid = false;
        uint32_t bid = 0;
    };
    Entry* find(const StreamKey& key);
    Entry* slot_for_new();
    void set(Entry& e, const StreamKey& key, uint32_t seq, int64_t up, int64_t now, Opt<uint32_t> bid);
    int64_t idle_forget_ms_;
    Entry entries_[kMaxStreams];
};

}  // namespace flybot
