#include "pha_npu.hpp"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
using namespace pha_npu;
void check(bool x,const char* message){if(!x)throw std::runtime_error(message);}
int main(){try{
    const auto pm=postprocess({0,0,0,0,0,15});
    check(pm["predicted_class"]=="PM"&&pm["predicted_class_index"]==5,"PM index/name");
    check(pm["classes"]==Json::array({"FM","AM","CW","noise","OOK","PM"}),"Six-class order");
    check(postprocess({0,0,0,0,15,0})["predicted_class"]=="OOK","OOK index retained");
    check(postprocess({0,0,0,15,0,0})["predicted_class"]=="noise","Noise index retained");
    double sum=0;for(const auto& p:pm["probabilities"])sum+=p.get<double>();
    check(std::abs(sum-1)<1e-12,"Normalized probabilities");
    const float extreme=std::numeric_limits<float>::max();
    check(postprocess({extreme,-extreme,0,0,0,0})["probabilities"][0].get<double>()==1.,"Stable softmax");
    for(auto invalid:std::vector<std::vector<float>>{{0,0,0,0},{0,0,0,0,0},{0,0,0,0,0,0,0},
        {0,0,0,0,0,std::numeric_limits<float>::infinity()},{0,0,0,0,0,std::numeric_limits<float>::quiet_NaN()}}){
        bool rejected=false;try{postprocess(invalid);}catch(const std::exception&){rejected=true;}
        check(rejected,"Invalid length/nonfinite rejection");
    }
    std::cout<<"PASS six-class PM/OOK/noise/output/softmax contract\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
