#pragma once
#include <stdexcept>
#include <string>

namespace pha_ipc {
inline void validate_name(const std::string& name) {
    if(name.size()<2||name.size()>120||name[0]!='/'||name.find('/',1)!=std::string::npos)
        throw std::runtime_error("IPC name must be /name with no further slash, at most120 characters");
}
} // namespace pha_ipc
