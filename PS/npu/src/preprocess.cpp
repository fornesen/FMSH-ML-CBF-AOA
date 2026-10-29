#include "pha_npu.hpp"
#include <algorithm>
#include <cfenv>
#include <cmath>
#include <stdexcept>

namespace pha_npu {
namespace {
constexpr double pi = 3.141592653589793238462643383279502884;
using Complex = std::complex<double>;

void radix2(std::vector<Complex>& a, bool inverse) {
    const std::size_t n = a.size();
    if (!n || (n & (n-1))) throw std::runtime_error("FFT size must be a power of two");
    for (std::size_t i=1,j=0; i<n; ++i) {
        std::size_t bit=n>>1;
        for (; j&bit; bit>>=1) j^=bit;
        j^=bit;
        if(i<j) std::swap(a[i],a[j]);
    }
    for (std::size_t length=2; length<=n; length<<=1) {
        const double angle=(inverse?2.:-2.)*pi/static_cast<double>(length);
        const Complex step(std::cos(angle),std::sin(angle));
        for(std::size_t base=0;base<n;base+=length) {
            Complex w(1,0);
            for(std::size_t j=0;j<length/2;++j) {
                const auto u=a[base+j],v=a[base+j+length/2]*w;
                a[base+j]=u+v; a[base+j+length/2]=u-v; w*=step;
            }
        }
    }
    if(inverse) for(auto& v:a) v/=static_cast<double>(n);
}

class Fourier {
    std::size_t n_, m_=1;
    std::vector<Complex> chirp_, kernel_;
public:
    explicit Fourier(std::size_t n):n_(n) {
        if(!(n_&(n_-1))) return;
        while(m_<2*n_-1) m_<<=1;
        chirp_.resize(n_); kernel_.resize(m_);
        for(std::size_t i=0;i<n_;++i) {
            // Reduce the angle to avoid large-angle trigonometric loss.
            const double angle=pi*static_cast<double>((i*i)%(2*n_))/static_cast<double>(n_);
            chirp_[i]=Complex(std::cos(angle),-std::sin(angle));
            const auto b=std::conj(chirp_[i]); kernel_[i]=b;
            if(i) kernel_[m_-i]=b;
        }
        radix2(kernel_,false);
    }
    std::vector<Complex> operator()(const std::vector<Complex>& x) const {
        if(x.size()!=n_) throw std::runtime_error("FFT input length mismatch");
        if(chirp_.empty()) { auto y=x; radix2(y,false); return y; }
        std::vector<Complex> a(m_);
        for(std::size_t i=0;i<n_;++i) a[i]=x[i]*chirp_[i];
        radix2(a,false);
        for(std::size_t i=0;i<m_;++i) a[i]*=kernel_[i];
        radix2(a,true); a.resize(n_);
        for(std::size_t i=0;i<n_;++i) a[i]*=chirp_[i];
        return a;
    }
};

std::uint8_t quantize(double value) {
    return static_cast<std::uint8_t>(std::nearbyint(std::clamp(value,0.,255.)));
}

std::array<std::uint8_t,kColumns> power_grid(const std::vector<Complex>& x,const Fourier& fft) {
    const int n=static_cast<int>(x.size());
    std::vector<Complex> weighted(n); double window_sum=0;
    for(int i=0;i<n;++i) {
        const double w=.5-.5*std::cos(2*pi*i/n);
        weighted[i]=x[i]*w; window_sum+=w;
    }
    auto spectrum=fft(weighted); std::vector<double> power(n);
    for(int i=0;i<n;++i) power[i]=std::norm(spectrum[(i+n/2)%n]/window_sum);
    std::array<std::uint8_t,kColumns> result{};
    for(int col=0;col<kColumns;++col) {
        const double frequency=-2000000.+(col+.5)*4000000./kColumns;
        const double position=(frequency+kFs/2)*n/kFs;
        const auto low=static_cast<int>(std::floor(position)); const double fraction=position-low;
        const double p=power[low]*(1-fraction)+power[low+1]*fraction;
        const double db=10*std::log10(std::max(p,1e-12));
        result[col]=quantize(255*std::clamp((db+75)/75,0.,1.));
    }
    return result;
}
}

Features preprocess(const Batch& batch) {
    if(std::fesetround(FE_TONEAREST)) throw std::runtime_error("Cannot select nearest-even rounding");
    Features f{{{"tf",{1,4,64,640},{},{}}, {"spectrum",{1,4,1,640},{},{}},
                 {"sequence",{1,4,1,48},{},{}}}};
    f[0].u8.resize(4*64*640); f[1].u8.resize(4*640); f[2].u8.resize(4*48);
    const Fourier short_fft(kWindow), full_fft(kSamples);
    for(int group=0;group<kRounds;++group) {
        const auto& x=batch.iq[group];
        if(x.size()!=kSamples) throw std::runtime_error("Expected 4032 IQ samples in each round");
        std::array<std::array<std::uint8_t,kColumns>,kFrames> native{};
        for(int frame=0;frame<kFrames;++frame) {
            const auto begin=x.begin()+frame*kHop;
            native[frame]=power_grid(std::vector<Complex>(begin,begin+kWindow),short_fft);
            const int center=kWindow/2+frame*kHop;
            double sum=0;
            for(int i=center-32;i<center+32;++i) sum+=std::norm(x[i]);
            f[2].u8[group*kFrames+frame]=quantize(255*std::clamp(std::sqrt(sum/64)/2,0.,1.));
        }
        const auto full=power_grid(x,full_fft);
        std::copy(full.begin(),full.end(),f[1].u8.begin()+group*kColumns);
        for(int row=0;row<64;++row) {
            // OpenCV 4.8 INTER_LINEAR uint8 vertical specialization. Its
            // two integer truncations differ from ordinary round(a*w+b*w).
            const double y=(row+.5)*.75-.5;
            int low=static_cast<int>(std::floor(y));
            int q=static_cast<int>(std::nearbyint((y-low)*8));
            if(low<0) { low=0; q=0; }
            if(low>=kFrames-1) { low=kFrames-1; q=0; }
            const int high=std::min(low+1,kFrames-1);
            for(int col=0;col<kColumns;++col) {
                const int a=native[low][col], b=native[high][col];
                f[0].u8[(group*64+row)*kColumns+col]=static_cast<std::uint8_t>(((a*(8-q)/2+b*q/2)+2)/4);
            }
        }
    }
    for(auto& tensor:f) {
        tensor.f32.reserve(tensor.u8.size());
        for(auto v:tensor.u8) tensor.f32.push_back(static_cast<float>(v)/255.f);
    }
    return f;
}

Json dump_features(const Features& f,const fs::path& prefix) {
    Json result=Json::array();
    for(const auto& t:f) {
        const auto base=prefix.string()+"_"+t.name;
        write_bytes_atomic(base+".u8",t.u8.data(),t.u8.size());
        write_bytes_atomic(base+".f32",t.f32.data(),t.f32.size()*sizeof(float));
        result.push_back({{"name",t.name},{"shape_nchw",t.shape},{"logical_elements",t.u8.size()},
            {"uint8_file",fs::absolute(base+".u8").string()},
            {"float32_file",fs::absolute(base+".f32").string()},
            {"uint8_sha256",sha256(t.u8)},{"float_scale","uint8/255 once"}});
    }
    return result;
}

Json postprocess(const std::vector<float>& logits) {
    if(logits.size()!=kClassCount) throw std::runtime_error("Model must return exactly 6 logits: FM/AM/CW/noise/OOK/PM");
    for(auto v:logits) if(!std::isfinite(v)) throw std::runtime_error("Nonfinite model output");
    const double largest=*std::max_element(logits.begin(),logits.end());
    std::array<double,kClassCount> p{}; double total=0;
    for(std::size_t i=0;i<kClassCount;++i) { p[i]=std::exp(static_cast<double>(logits[i])-largest); total+=p[i]; }
    for(auto& v:p) v/=total;
    const auto label=static_cast<int>(std::max_element(logits.begin(),logits.end())-logits.begin());
    return {{"classes",kClassNames},{"logits",logits},{"probabilities",p},
            {"predicted_class_index",label},{"predicted_class",kClassNames[label]}};
}
}
