#pragma once
#include "beam_weights.hpp"
#include "pl_contract.hpp"
namespace aoa_app {
// [波束控制新增] 与 reg_ctrl.v 的字地址 *4 一致。旧位流不支持配置回读，
// 版本不匹配即拒绝控制；不会因旧默认 FFFFFFFF 回读而误判成功。
template<class Write,class Read,class Delay>
void write_beam_registers(const BeamWeights& w,Write write,Read read,Delay delay) {
    if(!supported_pl_version(read(0xc0))) throw std::runtime_error("Unsupported PL beam register version");
    try {
      write(0x20,0);
      for(auto word:w.words) {
        const auto lo=static_cast<std::uint32_t>(word),hi=static_cast<std::uint32_t>(word>>32);
        write(0x24,lo);write(0x28,hi);
        if(read(0x24)!=lo || read(0x28)!=hi) throw std::runtime_error("Phase coefficient register readback mismatch");
        write(0x20,1);
        if(read(0x20)!=1) throw std::runtime_error("Phase enable readback mismatch");
        delay();write(0x20,0);delay();
        if(read(0x20)!=0) throw std::runtime_error("Phase enable stuck high");
      }
    } catch(...) {
        // [波束控制新增] 总线/回读失败也尝试撤销使能，禁止残留高电平锁存混合字。
        // 原异常继续上抛，本轮 DMA 不启动；不能把清零尝试当成硬件成功。
        try {write(0x20,0);} catch(...) {}
        throw;
    }
}

}
